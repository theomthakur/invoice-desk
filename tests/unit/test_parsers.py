from decimal import Decimal
from pathlib import Path

import pytest

from invoice_flow.config import Policy
from invoice_flow.findings import FindingCode
from invoice_flow.parsers import SourceError, load_source, parse_bytes
from invoice_flow.parsers.common import safe_filename


def parse(name: str, content: str | bytes, policy: Policy):
    data = content.encode() if isinstance(content, str) else content
    return parse_bytes(Path(name), data, policy)


def codes(draft) -> list[FindingCode]:
    return [f.code for f in draft.parse_findings]


# ------------------------------------------------------------------ supplied samples


def test_json_sample_keeps_exact_decimals(sample):
    draft = sample("invoice_1013.json").draft
    assert len(draft.line_items) == 8
    assert draft.total == Decimal("22562.80") and draft.tax_amount == Decimal("1472.80")
    assert draft.line_items[3].note == "Volume discount"
    assert draft.parse_findings == []


def test_json_nested_vendor_and_null_fields(sample):
    draft = sample("invoice_1009.json").draft
    assert draft.vendor_name is None and draft.due_date_raw is None  # unknown stays unknown
    assert draft.line_items[0].quantity == Decimal("-5")  # preserved so it can be reported
    assert draft.total == Decimal("-250.00")


def test_revision_label_is_read(sample):
    assert sample("invoice_1004_revised.json").draft.revision == "R1"
    assert sample("invoice_1004.json").draft.revision is None


def test_key_value_csv_keeps_repeated_items(sample):
    draft = sample("invoice_1006.csv").draft
    assert [(i.description, i.quantity, i.unit_price) for i in draft.line_items] == [
        ("WidgetA", Decimal(5), Decimal("250.00")),
        ("WidgetB", Decimal(3), Decimal("500.00")),
    ]
    assert draft.total == Decimal("2750.00") and draft.currency is None


def test_tabular_csv_reads_footer_rows(sample):
    draft = sample("invoice_1007.csv").draft
    assert len(draft.line_items) == 3
    assert (draft.subtotal, draft.tax_amount, draft.total) == (
        Decimal("14750.00"),
        Decimal("885.00"),
        Decimal("15525.00"),
    )
    assert draft.tax_rate == Decimal("0.06")  # from the "Tax (6%)" label
    assert draft.due_date_raw == "02/28/2026"  # raw text preserved, not guessed


def test_second_tabular_csv(sample):
    draft = sample("invoice_1015.csv").draft
    assert draft.invoice_number == "INV-1015" and len(draft.line_items) == 3
    assert draft.total == Decimal("6500.00")


def test_xml_sample(sample):
    draft = sample("invoice_1014.xml").draft
    assert draft.currency == "EUR" and draft.vendor_name == "TechParts International"
    assert [i.description for i in draft.line_items] == ["WidgetA", "WidgetB"]
    assert draft.total == Decimal("4125.00")


def test_text_and_pdf_stop_at_evidence(sample):
    for name in ("invoice_1001.txt", "invoice_1011.pdf", "invoice_1012.pdf"):
        parsed = sample(name)
        assert parsed.draft is None and parsed.source.text.strip()
    pdf = sample("invoice_1012.pdf").source
    assert pdf.pages and pdf.pages[0].page == 1
    assert "INV 1012" in pdf.text and "2O26" in pdf.text  # typos preserved for the extractor


def test_source_hash_is_stable_and_distinguishes_files(sample):
    a, b = sample("invoice_1011.txt").source, sample("invoice_1011.pdf").source
    assert a.byte_hash == sample("invoice_1011.txt").source.byte_hash
    assert a.byte_hash != b.byte_hash  # same invoice, different bytes


# ------------------------------------------------------------------ JSON edge cases


def test_json_rejects_duplicate_keys(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.json", '{"total": 10, "total": 99999}', policy)
    assert caught.value.code == "duplicate_key"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_json_rejects_non_finite_literals(policy, literal):
    with pytest.raises(SourceError) as caught:
        parse("x.json", f'{{"total": {literal}}}', policy)
    assert caught.value.code == "non_finite_number"


def test_json_malformed_and_wrong_shape(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.json", '{"total": ', policy)
    assert caught.value.code == "malformed"
    with pytest.raises(SourceError) as caught:
        parse("x.json", "[1, 2]", policy)
    assert caught.value.code == "unsupported_shape"


def test_json_float_text_does_not_lose_precision(policy):
    draft = parse("x.json", '{"invoice_number":"A","total": 0.1, "subtotal": 19.99}', policy).draft
    assert draft.total == Decimal("0.1") and draft.subtotal == Decimal("19.99")


def test_json_bad_values_become_findings_not_crashes(policy):
    body = (
        '{"invoice_number": true, "vendor": 5, "total": "12abc",'
        ' "line_items": [{"item": "WidgetA", "quantity": "two", "unit_price": 1.5e400}]}'
    )
    draft = parse("x.json", body, policy).draft
    fields = {(f.code, f.field) for f in draft.parse_findings}
    assert (FindingCode.SOURCE_FIELD_INVALID, "invoice_number") in fields
    assert (FindingCode.SOURCE_FIELD_INVALID, "total") in fields
    assert (FindingCode.SOURCE_FIELD_INVALID, "quantity") in fields
    assert draft.total is None and draft.line_items[0].quantity is None


def test_json_line_item_limit(policy):
    items = ",".join('{"item":"WidgetA","quantity":1,"unit_price":1}' for _ in range(201))
    with pytest.raises(SourceError) as caught:
        parse("x.json", f'{{"line_items":[{items}]}}', policy)
    assert caught.value.code == "too_many_lines"


# ------------------------------------------------------------------ XML safety


def test_xml_entity_expansion_is_refused(policy):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<invoice><header><invoice_number>&b;</invoice_number></header></invoice>"
    )
    with pytest.raises(SourceError) as caught:
        parse("x.xml", bomb, policy)
    assert caught.value.code == "unsafe_xml"


def test_xml_external_entity_is_refused(policy, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    xxe = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://{secret}">]>'
        "<invoice><header><vendor>&x;</vendor></header></invoice>"
    )
    with pytest.raises(SourceError) as caught:
        parse("x.xml", xxe, policy)
    assert caught.value.code == "unsafe_xml"


def test_xml_malformed_and_wrong_root(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.xml", "<invoice><oops></invoice>", policy)
    assert caught.value.code == "malformed"
    with pytest.raises(SourceError) as caught:
        parse("x.xml", "<order></order>", policy)
    assert caught.value.code == "unsupported_shape"


def test_xml_conflicting_repeated_values_become_a_finding(policy):
    body = "<invoice><header><total>10</total></header><totals><total>99</total></totals></invoice>"
    draft = parse("x.xml", body, policy).draft
    assert draft.total is None
    assert any(f.field == "total" for f in draft.parse_findings)


# ------------------------------------------------------------------ CSV edge cases


def test_csv_quoted_delimiters_and_crlf(policy):
    body = (
        'field,value\r\ninvoice_number,INV-9\r\nvendor,"Smith, Jones & Co."\r\n'
        "item,WidgetA\r\nquantity,2\r\nunit_price,250.00\r\ntotal,500.00\r\n"
    )
    draft = parse("x.csv", body, policy).draft
    assert draft.vendor_name == "Smith, Jones & Co." and draft.total == Decimal("500.00")


def test_csv_unknown_shape_and_field(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.csv", "a,b,c\n1,2,3\n", policy)
    assert caught.value.code == "unsupported_shape"
    draft = parse("x.csv", "field,value\nwhatever,1\ninvoice_number,X\n", policy).draft
    assert codes(draft) == [FindingCode.SOURCE_FIELD_INVALID]


def test_csv_item_field_before_any_item(policy):
    draft = parse("x.csv", "field,value\nquantity,5\ninvoice_number,X\n", policy).draft
    assert draft.line_items == [] and codes(draft) == [FindingCode.SOURCE_FIELD_INVALID]


def test_csv_conflicting_scalar_values(policy):
    body = (
        "Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total\n"
        "A1,V1,2026-01-01,2026-02-01,WidgetA,1,5,5\n"
        "A2,V1,2026-01-01,2026-02-01,WidgetB,1,5,5\n"
    )
    draft = parse("x.csv", body, policy).draft
    assert draft.invoice_number is None
    assert any(f.field == "invoice_number" for f in draft.parse_findings)


def test_csv_row_limit(policy):
    rows = "field,value\n" + "item,WidgetA\n" * 1001
    with pytest.raises(SourceError) as caught:
        parse("x.csv", rows, policy)
    assert caught.value.code == "too_many_rows"


# ------------------------------------------------------------------ PDF


def _pdf(text: str | None) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    if text:
        pdf.cell(0, 10, text)
    return bytes(pdf.output())


def test_pdf_with_text_is_extracted(policy):
    parsed = parse("x.pdf", _pdf("Invoice INV-77"), policy)
    assert "INV-77" in parsed.source.text and parsed.draft is None


def test_pdf_without_text_layer_is_unsupported_not_empty_success(policy):
    with pytest.raises(SourceError) as caught:
        parse("scan.pdf", _pdf(None), policy)
    assert caught.value.code == "no_text_layer"


def test_corrupt_pdf(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.pdf", b"%PDF-1.4\nthis is not really a pdf\n", policy)
    assert caught.value.code == "corrupt_pdf"


# ------------------------------------------------------------------ file handling safety


def test_extension_must_match_content(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.txt", _pdf("hello"), policy)
    assert caught.value.code == "extension_mismatch"
    with pytest.raises(SourceError) as caught:
        parse("x.pdf", b"just text", policy)
    assert caught.value.code == "extension_mismatch"
    with pytest.raises(SourceError) as caught:
        parse("x.json", "<invoice/>", policy)
    assert caught.value.code == "extension_mismatch"


def test_unsupported_extension_and_binary_and_empty(policy):
    with pytest.raises(SourceError) as caught:
        parse("x.exe", b"MZ", policy)
    assert caught.value.code == "unsupported_extension"
    with pytest.raises(SourceError) as caught:
        parse("x.txt", b"abc\x00def", policy)
    assert caught.value.code == "binary_content"
    with pytest.raises(SourceError) as caught:
        parse("x.txt", b"\xff\xfe\xfa", policy)
    assert caught.value.code == "bad_encoding"
    with pytest.raises(SourceError) as caught:
        parse("x.txt", "   \n ", policy)
    assert caught.value.code == "empty"


def test_file_size_limit_boundary(policy, tmp_path):
    # raise the text limit so the file-size limit is the one being exercised
    limits = policy.limits.model_copy(update={"max_text_chars": policy.limits.max_file_bytes * 2})
    loose = policy.model_copy(update={"limits": limits})
    limit = policy.limits.max_file_bytes
    exact = tmp_path / "exact.txt"
    exact.write_bytes(b"a" * limit)
    assert len(load_source(exact, loose).source.text) == limit  # exactly at the limit passes
    over = tmp_path / "over.txt"
    over.write_bytes(b"a" * (limit + 1))
    with pytest.raises(SourceError) as caught:
        load_source(over, loose)
    assert caught.value.code == "too_large"


def test_text_length_limit_boundary(policy, tmp_path):
    limit = policy.limits.max_text_chars
    exact = tmp_path / "exact.txt"
    exact.write_text("a" * limit)
    assert len(load_source(exact, policy).source.text) == limit
    over = tmp_path / "over.txt"
    over.write_text("a" * (limit + 1))
    with pytest.raises(SourceError) as caught:
        load_source(over, policy)
    assert caught.value.code == "too_large"


def test_traversal_and_symlink_are_refused(policy, tmp_path):
    inside = tmp_path / "inbox"
    inside.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret invoice")
    (inside / "ok.txt").write_text("fine")
    assert load_source(inside / "ok.txt", policy, root=inside).source.text == "fine"
    with pytest.raises(SourceError) as caught:
        load_source(inside / ".." / "outside.txt", policy, root=inside)
    assert caught.value.code == "outside_root"
    link = inside / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(SourceError) as caught:
        load_source(link, policy, root=inside)
    assert caught.value.code == "not_a_file"


def test_missing_file(policy, tmp_path):
    with pytest.raises(SourceError) as caught:
        load_source(tmp_path / "nope.json", policy)
    assert caught.value.code == "not_a_file"


def test_long_names_are_shortened_without_losing_the_extension():
    name = safe_filename("a" * 300 + ".json")
    assert len(name) <= 120 and name.endswith(".json") and name.startswith("aaa")
    assert safe_filename("b" * 200 + ".pdf").endswith(".pdf")
    assert safe_filename("c" * 200).endswith("c") and len(safe_filename("c" * 200)) <= 120


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("../../etc/passwd", "passwd"),
        ("a\\b\\c.txt", "c.txt"),
        ("in voice<>.txt", "in voice__.txt"),
        ("", "document"),
        ("...", "document"),
    ],
)
def test_safe_filename(raw, safe):
    assert safe_filename(raw) == safe


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_csv_accepts_spreadsheet_delimiters_without_losing_quoted_money(policy, delimiter):
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow(
        ["invoice_number", "vendor", "item", "quantity", "unit_price", "amount", "total"]
    )
    writer.writerow(["ALT-42", "Harbor Supply", "WidgetA", "2", "1,250.00", "2,500.00", "2,500.00"])
    draft = parse("invoice.csv", output.getvalue(), policy).draft
    assert draft.invoice_number == "ALT-42"
    assert draft.line_items[0].unit_price == Decimal("1250.00")
    assert draft.total == Decimal("2500.00")


def test_csv_duplicate_alias_columns_cannot_silently_replace_quantities(policy):
    with pytest.raises(SourceError, match="repeats a column"):
        parse("invoice.csv", "item,qty,quantity,unit_price\nWidgetA,100,1,25.00\n", policy)


def test_csv_unterminated_quote_is_a_visible_parse_failure(policy):
    with pytest.raises(SourceError, match="valid CSV"):
        parse("invoice.csv", 'field,value\nvendor,"unfinished\n', policy)


def test_csv_extra_nonempty_columns_are_not_silently_discarded(policy):
    draft = parse(
        "invoice.csv", "item,quantity,unit_price\nWidgetA,1,25.00,unexpected\n", policy
    ).draft
    assert any(f.field == "row" for f in draft.parse_findings)


def test_pdf_mixed_text_and_scan_cannot_silently_omit_the_scanned_page(policy):
    from fpdf import FPDF
    from PIL import Image

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Invoice header: additional line items follow on page 2.")
    pdf.add_page()
    pdf.image(Image.new("RGB", (100, 100), "white"), x=10, y=10, w=50)
    with pytest.raises(SourceError) as error:
        parse("mixed.pdf", bytes(pdf.output()), policy)
    assert error.value.code == "partial_text_layer"
    assert "Page 2" in error.value.message


def test_pdf_truly_blank_page_does_not_block_a_text_invoice(policy):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Invoice number: PAPER-43")
    pdf.add_page()
    parsed = parse("blank-page.pdf", bytes(pdf.output()), policy)
    assert "PAPER-43" in parsed.source.text
    assert len(parsed.source.pages) == 2


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_key_value_csv_does_not_truncate_a_value_at_an_extra_column(policy, delimiter):
    text = delimiter.join(["field", "value"]) + "\n" + delimiter.join(["total", "1", "000"]) + "\n"
    draft = parse("bad-export.csv", text, policy).draft
    assert draft.total is None
    assert any(f.field == "row" for f in draft.parse_findings)
