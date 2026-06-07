"""Unit tests for the structured e-invoice parser (no Tesseract / no network).

Synthetic but spec-shaped ZUGFeRD-CII and XRechnung-UBL fixtures with known
values verify that we extract the exact fields and pick the right amount.
"""
from datetime import date

import pytest

from app.einvoice import EInvoiceData, parse_einvoice_xml, find_einvoice


# --- CII (ZUGFeRD / Factur-X) -----------------------------------------------

CII_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>RE-2026-00421</ram:ID>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20260512</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Mustermann Bau GmbH</ram:Name>
      </ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>1804.40</ram:TaxBasisTotalAmount>
        <ram:GrandTotalAmount>2147.24</ram:GrandTotalAmount>
        <ram:DuePayableAmount>1147.24</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


def test_parse_cii_extracts_all_fields():
    d = parse_einvoice_xml(CII_XML)
    assert isinstance(d, EInvoiceData)
    assert d.profile == "cii"
    assert d.invoice_number == "RE-2026-00421"
    assert d.invoice_date == date(2026, 5, 12)
    assert d.vendor == "Mustermann Bau GmbH"
    assert d.currency == "EUR"


def test_parse_cii_amount_is_grand_total_not_due_payable():
    """GrandTotal is the invoice gross; DuePayable is reduced by prepayments."""
    d = parse_einvoice_xml(CII_XML)
    assert d.amount == 2147.24  # not 1147.24


def test_parse_cii_currency_fallback_from_attribute():
    xml = b"""<rsm:CrossIndustryInvoice
        xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
        xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
      <rsm:ExchangedDocument><ram:ID>X-1</ram:ID></rsm:ExchangedDocument>
      <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeSettlement>
          <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
            <ram:GrandTotalAmount currencyID="CHF">99.90</ram:GrandTotalAmount>
          </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        </ram:ApplicableHeaderTradeSettlement>
      </rsm:SupplyChainTradeTransaction>
    </rsm:CrossIndustryInvoice>"""
    d = parse_einvoice_xml(xml)
    assert d is not None
    assert d.amount == 99.90
    assert d.currency == "CHF"


# --- UBL (XRechnung) --------------------------------------------------------

UBL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ubl:Invoice
    xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017</cbc:CustomizationID>
  <cbc:ID>RE-2026-00999</cbc:ID>
  <cbc:IssueDate>2026-05-20</cbc:IssueDate>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>Handelsname (Fallback)</cbc:Name></cac:PartyName>
      <cac:PartyLegalEntity>
        <cbc:RegistrationName>Schmidt Elektro UG</cbc:RegistrationName>
      </cac:PartyLegalEntity>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
  </cac:InvoiceLine>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount>1000.00</cbc:LineExtensionAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">1190.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount>1190.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</ubl:Invoice>
"""


def test_parse_ubl_extracts_all_fields():
    d = parse_einvoice_xml(UBL_XML)
    assert d is not None
    assert d.profile == "ubl"
    assert d.invoice_number == "RE-2026-00999"  # root ID, not the line-item ID '1'
    assert d.invoice_date == date(2026, 5, 20)
    assert d.amount == 1190.00
    assert d.currency == "EUR"


def test_parse_ubl_prefers_registration_name_over_party_name():
    d = parse_einvoice_xml(UBL_XML)
    assert d.vendor == "Schmidt Elektro UG"


def test_parse_ubl_falls_back_to_party_name():
    xml = b"""<ubl:Invoice
        xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
        xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
        xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
      <cbc:ID>N-1</cbc:ID>
      <cac:AccountingSupplierParty><cac:Party>
        <cac:PartyName><cbc:Name>Nur Handelsname GbR</cbc:Name></cac:PartyName>
      </cac:Party></cac:AccountingSupplierParty>
      <cac:LegalMonetaryTotal>
        <cbc:TaxInclusiveAmount>50.00</cbc:TaxInclusiveAmount>
      </cac:LegalMonetaryTotal>
    </ubl:Invoice>"""
    d = parse_einvoice_xml(xml)
    assert d is not None
    assert d.vendor == "Nur Handelsname GbR"


# --- Negative / robustness --------------------------------------------------

def test_non_einvoice_xml_returns_none():
    assert parse_einvoice_xml(b"<foo><bar>hello</bar></foo>") is None


def test_empty_input_returns_none():
    assert parse_einvoice_xml(b"") is None
    assert parse_einvoice_xml(None) is None  # type: ignore[arg-type]


def test_garbage_is_not_parsed_as_invoice():
    assert parse_einvoice_xml(b"not xml at all <<<") is None


def test_bare_root_without_usable_fields_returns_none():
    xml = b"""<rsm:CrossIndustryInvoice
        xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>"""
    assert parse_einvoice_xml(xml) is None


def test_billion_laughs_entity_expansion_is_blocked():
    """defusedxml must refuse DTD entity expansion (DoS vector) → we return None,
    never expand."""
    malicious = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">
      <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">
    ]>
    <rsm:CrossIndustryInvoice
        xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
        xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
      <rsm:ExchangedDocument><ram:ID>&lol3;</ram:ID></rsm:ExchangedDocument>
    </rsm:CrossIndustryInvoice>"""
    assert parse_einvoice_xml(malicious) is None


def test_credit_note_is_not_auto_imported():
    """A CreditNote (Gutschrift/Storno) carries a positive TaxInclusiveAmount but
    represents a credit — importing it as a positive cost would be wrong, so we
    return None and let OCR/manual handle it."""
    xml = b"""<ubl:CreditNote
        xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
        xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
        xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
      <cbc:ID>GS-7</cbc:ID>
      <cbc:IssueDate>2026-03-01</cbc:IssueDate>
      <cac:LegalMonetaryTotal>
        <cbc:TaxInclusiveAmount>23.80</cbc:TaxInclusiveAmount>
      </cac:LegalMonetaryTotal>
    </ubl:CreditNote>"""
    assert parse_einvoice_xml(xml) is None


def test_oversized_xml_is_rejected():
    big = b"<x>" + b"A" * (13 * 1024 * 1024) + b"</x>"
    assert parse_einvoice_xml(big) is None


def test_cii_credit_note_typecode_is_refused():
    """A CII Gutschrift keeps the CrossIndustryInvoice root and is only
    distinguished by ExchangedDocument/TypeCode 381 — must not be imported."""
    xml = b"""<rsm:CrossIndustryInvoice
        xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
        xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
      <rsm:ExchangedDocument>
        <ram:ID>GS-2026-1</ram:ID>
        <ram:TypeCode>381</ram:TypeCode>
      </rsm:ExchangedDocument>
      <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeAgreement>
          <ram:SellerTradeParty><ram:Name>Bau GmbH</ram:Name></ram:SellerTradeParty>
        </ram:ApplicableHeaderTradeAgreement>
        <ram:ApplicableHeaderTradeSettlement>
          <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
            <ram:GrandTotalAmount>500.00</ram:GrandTotalAmount>
          </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        </ram:ApplicableHeaderTradeSettlement>
      </rsm:SupplyChainTradeTransaction>
    </rsm:CrossIndustryInvoice>"""
    assert parse_einvoice_xml(xml) is None


def test_cii_invoice_typecode_380_is_accepted():
    xml = CII_XML.replace(
        b"<ram:ID>RE-2026-00421</ram:ID>",
        b"<ram:ID>RE-2026-00421</ram:ID><ram:TypeCode>380</ram:TypeCode>",
    )
    d = parse_einvoice_xml(xml)
    assert d is not None and d.amount == 2147.24


def test_ubl_invoice_with_credit_typecode_is_refused():
    xml = UBL_XML.replace(
        b"<cbc:ID>RE-2026-00999</cbc:ID>",
        b"<cbc:InvoiceTypeCode>381</cbc:InvoiceTypeCode><cbc:ID>RE-2026-00999</cbc:ID>",
    )
    assert parse_einvoice_xml(xml) is None


# --- Decompression-bomb defense (_decode_stream_bounded) ---------------------

class _FakeStream:
    """Minimal stand-in for a pypdf stream object (raw bytes + /Filter)."""
    def __init__(self, data, filt=None):
        self._data = data
        self._filt = filt

    def get(self, key):
        return self._filt if key == "/Filter" else None


def test_decode_uncompressed_stream():
    from app.einvoice import _decode_stream_bounded
    assert _decode_stream_bounded(_FakeStream(b"<xml/>")) == b"<xml/>"


def test_decode_flate_stream():
    import zlib
    from app.einvoice import _decode_stream_bounded
    payload = b"<rsm:CrossIndustryInvoice/>"
    s = _FakeStream(zlib.compress(payload), filt="/FlateDecode")
    assert _decode_stream_bounded(s) == payload


def test_decode_flate_bomb_is_rejected():
    """A small compressed stream that inflates past the cap must be skipped, not
    expanded into RAM."""
    import zlib
    from app.einvoice import _decode_stream_bounded
    bomb = zlib.compress(b"\x00" * (50 * 1024 * 1024))  # ~50 MiB of zeros -> tiny
    assert len(bomb) < 1_000_000  # the compressed form is small (that's the trap)
    s = _FakeStream(bomb, filt="/FlateDecode")
    assert _decode_stream_bounded(s) is None


def test_decode_unknown_filter_is_skipped():
    from app.einvoice import _decode_stream_bounded
    assert _decode_stream_bounded(_FakeStream(b"data", filt="/LZWDecode")) is None


def test_decode_missing_data_is_none():
    from app.einvoice import _decode_stream_bounded

    class NoData:
        def get(self, key):
            return None
    assert _decode_stream_bounded(NoData()) is None


def test_conflicting_einvoice_xmls_fall_back_to_ocr(tmp_path):
    """Two e-invoice XMLs with different totals in one PDF -> bail to OCR."""
    pypdf = pytest.importorskip("pypdf")
    other_cii = CII_XML.replace(b"2147.24", b"999.00")
    pdf = tmp_path / "ambiguous.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=210, height=297)
    writer.add_attachment("factur-x.xml", CII_XML)
    writer.add_attachment("zugferd-invoice.xml", other_cii)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert find_einvoice(pdf, "application/pdf") is None


# --- Integration: embedded XML in a real PDF (ZUGFeRD/Factur-X) --------------

def test_find_einvoice_in_zugferd_pdf(tmp_path):
    """A PDF/A-3-style attachment is found + parsed without any OCR.

    pypdf builds the PDF; no poppler/tesseract needed — proving the deterministic
    path works end-to-end on a hybrid PDF.
    """
    pypdf = pytest.importorskip("pypdf")
    pdf = tmp_path / "zugferd.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=210, height=297)
    writer.add_attachment("factur-x.xml", CII_XML)
    with open(pdf, "wb") as fh:
        writer.write(fh)

    d = find_einvoice(pdf, "application/pdf")
    assert d is not None
    assert d.profile == "cii"
    assert d.amount == 2147.24
    assert d.vendor == "Mustermann Bau GmbH"
    assert d.invoice_number == "RE-2026-00421"


def test_find_einvoice_standalone_xml(tmp_path):
    xml_file = tmp_path / "rechnung.xml"
    xml_file.write_bytes(UBL_XML)
    d = find_einvoice(xml_file, "application/xml")
    assert d is not None
    assert d.profile == "ubl"
    assert d.amount == 1190.00


def test_extract_short_circuits_full_cii_xml(tmp_path):
    from app import ocr
    f = tmp_path / "full.xml"
    f.write_bytes(CII_XML)
    res = ocr.extract(f, "application/xml")
    assert res.engine == "einvoice-cii"
    assert res.amount == 2147.24


def test_extract_gate_does_not_short_circuit_without_vendor(tmp_path):
    """Amount + number but no vendor → not confident enough for 0.99; falls
    through (for a standalone XML that means engine 'failed', no wrong import)."""
    from app import ocr
    xml = b"""<rsm:CrossIndustryInvoice
        xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
        xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
      <rsm:ExchangedDocument><ram:ID>NOVENDOR-1</ram:ID></rsm:ExchangedDocument>
      <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeSettlement>
          <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
            <ram:GrandTotalAmount>100.00</ram:GrandTotalAmount>
          </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        </ram:ApplicableHeaderTradeSettlement>
      </rsm:SupplyChainTradeTransaction>
    </rsm:CrossIndustryInvoice>"""
    f = tmp_path / "novendor.xml"
    f.write_bytes(xml)
    res = ocr.extract(f, "application/xml")
    assert not (res.engine or "").startswith("einvoice")


def test_find_einvoice_plain_pdf_without_xml_returns_none(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    pdf = tmp_path / "plain.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=210, height=297)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert find_einvoice(pdf, "application/pdf") is None


# --- Line items (Rechnungspositionen, EN 16931 BG-25) -----------------------

CII_XML_LINES = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <rsm:ExchangedDocument><ram:ID>RE-LINES-1</ram:ID></rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>1</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Dachziegel rot</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>1.20</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery>
        <ram:BilledQuantity unitCode="C62">500</ram:BilledQuantity>
      </ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:RateApplicablePercent>19</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>600.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>2</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Dachdecker-Stunden</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeDelivery>
        <ram:BilledQuantity unitCode="HUR">8</ram:BilledQuantity>
      </ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>440.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>Dach GmbH</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:GrandTotalAmount>1237.60</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""

UBL_XML_LINES = b"""<?xml version="1.0" encoding="UTF-8"?>
<ubl:Invoice
    xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>RE-UBL-LINES</cbc:ID>
  <cbc:IssueDate>2026-05-20</cbc:IssueDate>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyLegalEntity>
    <cbc:RegistrationName>Elektro UG</cbc:RegistrationName>
  </cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="MTR">50</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount>125.00</cbc:LineExtensionAmount>
    <cac:Price><cbc:PriceAmount>2.50</cbc:PriceAmount></cac:Price>
    <cac:Item>
      <cbc:Name>NYM-J 3x1,5 Kabel</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:Percent>19</cbc:Percent></cac:ClassifiedTaxCategory>
    </cac:Item>
  </cac:InvoiceLine>
  <cac:InvoiceLine>
    <cbc:ID>2</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">3</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount>89.70</cbc:LineExtensionAmount>
    <cac:Item>
      <cbc:Description>Steckdose Unterputz</cbc:Description>
    </cac:Item>
  </cac:InvoiceLine>
  <cac:LegalMonetaryTotal>
    <cbc:TaxInclusiveAmount>255.50</cbc:TaxInclusiveAmount>
  </cac:LegalMonetaryTotal>
</ubl:Invoice>
"""


def test_cii_lines_extracted():
    d = parse_einvoice_xml(CII_XML_LINES)
    assert d is not None and len(d.lines) == 2
    l0 = d.lines[0]
    assert l0.position == "1"
    assert l0.description == "Dachziegel rot"
    assert l0.quantity == 500.0
    assert l0.unit == "C62" and l0.unit_label == "Stk"
    assert l0.unit_price == 1.20
    assert l0.net_amount == 600.00
    assert l0.vat_percent == 19.0


def test_cii_line_without_price_or_vat():
    d = parse_einvoice_xml(CII_XML_LINES)
    l1 = d.lines[1]
    assert l1.description == "Dachdecker-Stunden"
    assert l1.quantity == 8.0
    assert l1.unit_label == "Std"   # HUR -> Std
    assert l1.net_amount == 440.00
    assert l1.unit_price is None
    assert l1.vat_percent is None


def test_ubl_lines_extracted():
    d = parse_einvoice_xml(UBL_XML_LINES)
    assert d is not None and len(d.lines) == 2
    l0 = d.lines[0]
    assert l0.position == "1"
    assert l0.description == "NYM-J 3x1,5 Kabel"
    assert l0.quantity == 50.0
    assert l0.unit == "MTR" and l0.unit_label == "m"
    assert l0.unit_price == 2.50
    assert l0.net_amount == 125.00
    assert l0.vat_percent == 19.0


def test_ubl_line_description_fallback():
    d = parse_einvoice_xml(UBL_XML_LINES)
    l1 = d.lines[1]
    assert l1.description == "Steckdose Unterputz"  # Item/Description fallback
    assert l1.unit_label == "Stk"  # C62
    assert l1.net_amount == 89.70
    assert l1.unit_price is None
    assert l1.vat_percent is None


def test_unknown_unit_code_keeps_raw_code_as_label():
    xml = UBL_XML_LINES.replace(b'unitCode="MTR"', b'unitCode="ZX9"')
    d = parse_einvoice_xml(xml)
    assert d.lines[0].unit == "ZX9"
    assert d.lines[0].unit_label == "ZX9"


def test_header_only_invoices_have_no_lines():
    # The plain header fixtures (and the degenerate ID-only UBL stub) yield no lines.
    assert parse_einvoice_xml(CII_XML).lines == []
    assert parse_einvoice_xml(UBL_XML).lines == []  # <InvoiceLine><ID>1</ID></InvoiceLine> is skipped


def test_lines_do_not_break_header_extraction():
    d = parse_einvoice_xml(CII_XML_LINES)
    assert d.invoice_number == "RE-LINES-1"
    assert d.vendor == "Dach GmbH"
    assert d.amount == 1237.60  # gross header total, independent of net line sums


def test_line_count_is_capped_and_flagged():
    from app.einvoice import _MAX_LINES
    one_line = (
        b"<cac:InvoiceLine><cbc:ID>{i}</cbc:ID>"
        b"<cbc:LineExtensionAmount>1.00</cbc:LineExtensionAmount>"
        b"<cac:Item><cbc:Name>x</cbc:Name></cac:Item></cac:InvoiceLine>"
    )
    body = b"".join(one_line.replace(b"{i}", str(i).encode()) for i in range(_MAX_LINES + 50))
    xml = (
        b'<ubl:Invoice xmlns:ubl="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"'
        b' xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"'
        b' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        b"<cbc:ID>BIG-1</cbc:ID>"
        + body
        + b"<cac:LegalMonetaryTotal><cbc:TaxInclusiveAmount>9.99</cbc:TaxInclusiveAmount>"
        b"</cac:LegalMonetaryTotal></ubl:Invoice>"
    )
    d = parse_einvoice_xml(xml)
    assert len(d.lines) == _MAX_LINES
    assert d.lines_truncated is True


def test_find_einvoice_standalone_xml_carries_lines(tmp_path):
    xml_file = tmp_path / "lines.xml"
    xml_file.write_bytes(CII_XML_LINES)
    d = find_einvoice(xml_file, "application/xml")
    assert d is not None
    assert [ln.description for ln in d.lines] == ["Dachziegel rot", "Dachdecker-Stunden"]
