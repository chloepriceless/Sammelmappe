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


def test_credit_note_root_is_parsed_as_ubl():
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
    d = parse_einvoice_xml(xml)
    assert d is not None
    assert d.profile == "ubl"
    assert d.invoice_number == "GS-7"
    assert d.amount == 23.80


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


def test_find_einvoice_plain_pdf_without_xml_returns_none(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    pdf = tmp_path / "plain.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=210, height=297)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert find_einvoice(pdf, "application/pdf") is None
