#!/usr/bin/env python3
"""Synthesize a small labeled corpus of enterprise documents as PDFs.

For every blueprint we emit:
  corpus/<name>/<name>.pdf       — the rendered document
  corpus/<name>/expected.json    — the ground-truth field values

Field values in expected.json correspond to the property keys in
blueprints/<name>.json so run_tests.py can diff inferred vs expected.

Documents are deliberately content-rich and visually plausible so BDA's
extraction has fair signal to work with. They are not real documents.
"""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
CORPUS.mkdir(exist_ok=True)

styles = getSampleStyleSheet()
title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=20, spaceAfter=12)
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceAfter=6)
body = styles["BodyText"]


def _doc(out_pdf: Path) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(out_pdf),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )


def _kv_table(rows):
    t = Table(rows, colWidths=[1.9 * inch, 4.6 * inch])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _line_table(header, rows, col_widths):
    data = [header] + rows
    t = Table(data, colWidths=col_widths)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return t


# ---------------------------------------------------------------------------
# 1. Invoice
# ---------------------------------------------------------------------------
def build_invoice():
    out = CORPUS / "invoice"
    out.mkdir(exist_ok=True)
    pdf_path = out / "invoice.pdf"
    expected = {
        "invoice_number": "INV-2024-00428",
        "invoice_date": "2024-09-12",
        "due_date": "2024-10-12",
        "purchase_order": "PO-77531",
        "vendor_name": "Aurora Cloud Solutions, LLC",
        "vendor_address": "120 Market Street, Suite 800, San Francisco, CA 94105",
        "vendor_tax_id": "EIN 84-2937104",
        "bill_to_name": "Northwind Logistics Inc.",
        "bill_to_address": "950 Industrial Way, Reno, NV 89506",
        "currency": "USD",
        "subtotal": 18750.00,
        "tax_total": 1640.63,
        "total_due": 20390.63,
        "payment_terms": "Net 30",
        "line_items": [
            {"description": "Cloud Architecture Review (40 hrs)", "quantity": 40, "unit_price": 250.00, "amount": 10000.00},
            {"description": "Data pipeline implementation (25 hrs)", "quantity": 25, "unit_price": 275.00, "amount": 6875.00},
            {"description": "Knowledge transfer workshop", "quantity": 1, "unit_price": 1875.00, "amount": 1875.00},
        ],
    }

    doc = _doc(pdf_path)
    story = [
        Paragraph("INVOICE", title_style),
        Paragraph(f"<b>Invoice #:</b> {expected['invoice_number']}", body),
        Paragraph(f"<b>Invoice Date:</b> {expected['invoice_date']} &nbsp; <b>Due Date:</b> {expected['due_date']}", body),
        Paragraph(f"<b>Purchase Order:</b> {expected['purchase_order']}", body),
        Spacer(1, 12),
        _kv_table([
            ["From (Vendor)", f"{expected['vendor_name']}<br/>{expected['vendor_address']}<br/>{expected['vendor_tax_id']}"],
            ["Bill To", f"{expected['bill_to_name']}<br/>{expected['bill_to_address']}"],
        ]),
    ]
    # Render multi-line cells via paragraphs
    story[-1] = _kv_table([
        [Paragraph("From (Vendor)", body), Paragraph(f"{expected['vendor_name']}<br/>{expected['vendor_address']}<br/>{expected['vendor_tax_id']}", body)],
        [Paragraph("Bill To", body), Paragraph(f"{expected['bill_to_name']}<br/>{expected['bill_to_address']}", body)],
    ])
    story += [
        Spacer(1, 14),
        Paragraph("Line Items", h2),
        _line_table(
            ["Description", "Qty", "Unit Price", "Amount"],
            [[li["description"], f"{li['quantity']}", f"${li['unit_price']:,.2f}", f"${li['amount']:,.2f}"] for li in expected["line_items"]],
            [3.6 * inch, 0.7 * inch, 1.2 * inch, 1.2 * inch],
        ),
        Spacer(1, 12),
        _kv_table([
            ["Subtotal (USD)", f"${expected['subtotal']:,.2f}"],
            ["Tax (8.75%)", f"${expected['tax_total']:,.2f}"],
            ["Total Due (USD)", f"${expected['total_due']:,.2f}"],
            ["Payment Terms", expected["payment_terms"]],
        ]),
        Spacer(1, 18),
        Paragraph("Make checks payable to Aurora Cloud Solutions, LLC. Wire instructions on request.", body),
    ]
    doc.build(story)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"  invoice -> {pdf_path}")


# ---------------------------------------------------------------------------
# 2. Bill of Lading
# ---------------------------------------------------------------------------
def build_bol():
    out = CORPUS / "bill_of_lading"
    out.mkdir(exist_ok=True)
    pdf_path = out / "bill_of_lading.pdf"
    expected = {
        "bol_number": "BOL-998341",
        "bol_date": "2024-08-22",
        "carrier_name": "Pacific West Freight Lines",
        "carrier_scac": "PWFL",
        "shipper_name": "Cascade Manufacturing Co.",
        "shipper_address": "4400 Industrial Pkwy, Tacoma, WA 98424",
        "consignee_name": "Sunbelt Distributors LLC",
        "consignee_address": "8210 South Loop, Houston, TX 77033",
        "third_party_billing": "Cascade Manufacturing Co.",
        "freight_terms": "Prepaid",
        "pro_number": "PWFL-7710-44",
        "trailer_number": "PWFL-204918",
        "seal_number": "SEAL-44217",
        "total_packages": 24,
        "total_weight": 6800.0,
        "hazmat": False,
        "items": [
            {"description": "Cardboard boxes - small electronics", "package_count": 12, "weight": 2400.0, "weight_unit": "LB", "nmfc_class": "92.5"},
            {"description": "Wooden crates - industrial valves", "package_count": 8, "weight": 3200.0, "weight_unit": "LB", "nmfc_class": "70"},
            {"description": "Pallets - replacement parts", "package_count": 4, "weight": 1200.0, "weight_unit": "LB", "nmfc_class": "85"},
        ],
    }

    doc = _doc(pdf_path)
    story = [
        Paragraph("UNIFORM BILL OF LADING - NOT NEGOTIABLE", title_style),
        _kv_table([
            ["BoL Number", expected["bol_number"]],
            ["Date", expected["bol_date"]],
            ["Carrier", f"{expected['carrier_name']} (SCAC {expected['carrier_scac']})"],
            ["PRO Number", expected["pro_number"]],
            ["Trailer / Seal", f"{expected['trailer_number']} / {expected['seal_number']}"],
            ["Freight Terms", expected["freight_terms"]],
            ["Third-Party Bill-To", expected["third_party_billing"]],
            ["Hazmat?", "NO" if not expected["hazmat"] else "YES"],
        ]),
        Spacer(1, 12),
        _kv_table([
            [Paragraph("Shipper", body), Paragraph(f"{expected['shipper_name']}<br/>{expected['shipper_address']}", body)],
            [Paragraph("Consignee", body), Paragraph(f"{expected['consignee_name']}<br/>{expected['consignee_address']}", body)],
        ]),
        Spacer(1, 14),
        Paragraph("Goods", h2),
        _line_table(
            ["Description", "Pkgs", "Weight", "Unit", "NMFC"],
            [[i["description"], f"{i['package_count']}", f"{i['weight']:,.0f}", i["weight_unit"], i["nmfc_class"]] for i in expected["items"]],
            [3.0 * inch, 0.7 * inch, 1.0 * inch, 0.7 * inch, 1.0 * inch],
        ),
        Spacer(1, 10),
        _kv_table([
            ["Total Packages", str(expected["total_packages"])],
            ["Total Weight (LB)", f"{expected['total_weight']:,.0f}"],
        ]),
    ]
    doc.build(story)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"  bill_of_lading -> {pdf_path}")


# ---------------------------------------------------------------------------
# 3. Schedule K-1 (Form 1065)
# ---------------------------------------------------------------------------
def build_k1():
    out = CORPUS / "k1_1065"
    out.mkdir(exist_ok=True)
    pdf_path = out / "k1_1065.pdf"
    expected = {
        "tax_year": "2024",
        "partnership_name": "Bluestone Capital Partners, LP",
        "partnership_ein": "47-1928374",
        "partnership_address": "100 Pine Street, Suite 2200, San Francisco, CA 94111",
        "partner_ssn_or_ein": "123-45-6789",
        "partner_name": "Eleanor R. Whitfield",
        "partner_address": "215 Maple Lane, Palo Alto, CA 94301",
        "partner_type": "Limited",
        "is_foreign_partner": False,
        "profit_share_begin": 5.0,
        "profit_share_end": 5.0,
        "loss_share_end": 5.0,
        "capital_share_end": 4.875,
        "capital_account_beginning": 250000.00,
        "capital_account_ending": 287500.00,
        "ordinary_business_income": 42000.00,
        "net_rental_real_estate_income": 0.00,
        "interest_income": 1850.00,
        "ordinary_dividends": 920.00,
        "qualified_dividends": 780.00,
        "net_short_term_capital_gain": 0.00,
        "net_long_term_capital_gain": 12500.00,
        "self_employment_earnings": 42000.00,
        "distributions": 30000.00,
    }

    doc = _doc(pdf_path)
    story = [
        Paragraph(f"Schedule K-1 (Form 1065) — Tax Year {expected['tax_year']}", title_style),
        Paragraph("Partner's Share of Income, Deductions, Credits, etc.", body),
        Spacer(1, 10),
        Paragraph("Part I — Information About the Partnership", h2),
        _kv_table([
            ["A. Partnership name", expected["partnership_name"]],
            ["A. Partnership EIN", expected["partnership_ein"]],
            ["B. Address", expected["partnership_address"]],
        ]),
        Spacer(1, 10),
        Paragraph("Part II — Information About the Partner", h2),
        _kv_table([
            ["E. Partner SSN/EIN", expected["partner_ssn_or_ein"]],
            ["F. Partner name", expected["partner_name"]],
            ["F. Partner address", expected["partner_address"]],
            ["G. Partner type", expected["partner_type"]],
            ["H1. Foreign partner?", "Yes" if expected["is_foreign_partner"] else "No"],
            ["J. Profit share (begin / end)", f"{expected['profit_share_begin']}% / {expected['profit_share_end']}%"],
            ["J. Loss share (end)", f"{expected['loss_share_end']}%"],
            ["J. Capital share (end)", f"{expected['capital_share_end']}%"],
            ["L. Capital account (begin)", f"${expected['capital_account_beginning']:,.2f}"],
            ["L. Capital account (end)", f"${expected['capital_account_ending']:,.2f}"],
        ]),
        Spacer(1, 10),
        Paragraph("Part III — Partner's Share of Current Year Income, Deductions, Credits", h2),
        _kv_table([
            ["1. Ordinary business income (loss)", f"${expected['ordinary_business_income']:,.2f}"],
            ["2. Net rental real estate income (loss)", f"${expected['net_rental_real_estate_income']:,.2f}"],
            ["5. Interest income", f"${expected['interest_income']:,.2f}"],
            ["6a. Ordinary dividends", f"${expected['ordinary_dividends']:,.2f}"],
            ["6b. Qualified dividends", f"${expected['qualified_dividends']:,.2f}"],
            ["8. Net short-term capital gain (loss)", f"${expected['net_short_term_capital_gain']:,.2f}"],
            ["9a. Net long-term capital gain (loss)", f"${expected['net_long_term_capital_gain']:,.2f}"],
            ["14. Self-employment earnings (Code A)", f"${expected['self_employment_earnings']:,.2f}"],
            ["19. Distributions (Code A)", f"${expected['distributions']:,.2f}"],
        ]),
    ]
    doc.build(story)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"  k1_1065 -> {pdf_path}")


# ---------------------------------------------------------------------------
# 4. Lease Abstract (commercial)
# ---------------------------------------------------------------------------
def build_lease():
    out = CORPUS / "lease_abstract"
    out.mkdir(exist_ok=True)
    pdf_path = out / "lease_abstract.pdf"
    expected = {
        "landlord_name": "Riverbend Office Holdings, LLC",
        "tenant_name": "Helix Diagnostics Inc.",
        "guarantor_name": "Helix Holdings Inc.",
        "premises_address": "1880 Pacific Avenue, Suite 400, San Diego, CA 92101",
        "rentable_square_feet": 14250,
        "use_clause": "General office and clinical lab use, including ancillary R&D",
        "lease_type": "NNN",
        "lease_commencement_date": "2024-11-01",
        "lease_expiration_date": "2031-10-31",
        "term_months": 84,
        "security_deposit": 95000.00,
        "renewal_options": "Two (2) successive five (5) year renewal options at 95% of fair market rent",
        "early_termination": "One-time termination right effective month 60 with 12 months' notice and $250,000 fee",
        "operating_expense_recovery": "Tenant pays pro-rata share of operating expenses, taxes, and insurance over a 2024 base year",
        "tenant_improvement_allowance": 855000.00,
        "rent_schedule": [
            {"period_start": "2024-11-01", "period_end": "2025-10-31", "monthly_rent": 47500.00, "annual_rent": 570000.00},
            {"period_start": "2025-11-01", "period_end": "2026-10-31", "monthly_rent": 48925.00, "annual_rent": 587100.00},
            {"period_start": "2026-11-01", "period_end": "2027-10-31", "monthly_rent": 50392.75, "annual_rent": 604713.00},
            {"period_start": "2027-11-01", "period_end": "2028-10-31", "monthly_rent": 51904.53, "annual_rent": 622854.39},
            {"period_start": "2028-11-01", "period_end": "2029-10-31", "monthly_rent": 53461.67, "annual_rent": 641540.02},
            {"period_start": "2029-11-01", "period_end": "2030-10-31", "monthly_rent": 55065.52, "annual_rent": 660786.22},
            {"period_start": "2030-11-01", "period_end": "2031-10-31", "monthly_rent": 56717.49, "annual_rent": 680609.81},
        ],
    }

    doc = _doc(pdf_path)
    story = [
        Paragraph("Lease Abstract", title_style),
        _kv_table([
            ["Landlord", expected["landlord_name"]],
            ["Tenant", expected["tenant_name"]],
            ["Guarantor", expected["guarantor_name"]],
            ["Premises", expected["premises_address"]],
            ["Rentable Square Feet", f"{expected['rentable_square_feet']:,}"],
            ["Permitted Use", expected["use_clause"]],
            ["Lease Type", expected["lease_type"]],
            ["Commencement Date", expected["lease_commencement_date"]],
            ["Expiration Date", expected["lease_expiration_date"]],
            ["Term (months)", str(expected["term_months"])],
            ["Security Deposit", f"${expected['security_deposit']:,.2f}"],
            ["Tenant Improvement Allowance", f"${expected['tenant_improvement_allowance']:,.2f}"],
        ]),
        Spacer(1, 10),
        Paragraph("Key Clauses", h2),
        _kv_table([
            [Paragraph("Renewal Options", body), Paragraph(expected["renewal_options"], body)],
            [Paragraph("Early Termination", body), Paragraph(expected["early_termination"], body)],
            [Paragraph("Operating Expense Recovery", body), Paragraph(expected["operating_expense_recovery"], body)],
        ]),
        Spacer(1, 12),
        Paragraph("Base Rent Schedule", h2),
        _line_table(
            ["Start", "End", "Monthly", "Annual"],
            [[s["period_start"], s["period_end"], f"${s['monthly_rent']:,.2f}", f"${s['annual_rent']:,.2f}"] for s in expected["rent_schedule"]],
            [1.4 * inch, 1.4 * inch, 1.5 * inch, 1.7 * inch],
        ),
    ]
    doc.build(story)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"  lease_abstract -> {pdf_path}")


# ---------------------------------------------------------------------------
# 5. Master Services Agreement
# ---------------------------------------------------------------------------
def build_msa():
    out = CORPUS / "msa"
    out.mkdir(exist_ok=True)
    pdf_path = out / "msa.pdf"
    expected = {
        "agreement_title": "Master Services Agreement",
        "effective_date": "2024-07-15",
        "execution_date": "2024-07-18",
        "customer_name": "Northstar Health Systems, Inc.",
        "customer_address": "200 North Wells Street, Chicago, IL 60606",
        "customer_state_of_formation": "Delaware",
        "provider_name": "Meridian Data Services, LLC",
        "provider_address": "55 Innovation Way, Boston, MA 02210",
        "provider_state_of_formation": "Massachusetts",
        "initial_term": "36 months",
        "renewal_term": "Successive 12-month auto-renewals unless terminated with 60 days' notice",
        "termination_for_convenience_notice_days": 90,
        "payment_terms": "Net 45 from invoice date",
        "late_fee_rate": "1.5% per month or the maximum permitted by law",
        "liability_cap": "Aggregate liability capped at fees paid by Customer in the prior twelve (12) months",
        "consequential_damages_waived": True,
        "indemnification_summary": "Mutual indemnities for third-party IP infringement and bodily injury / property damage from a party's negligence",
        "ip_ownership": "Customer owns deliverables; Provider retains pre-existing IP and grants a perpetual, non-exclusive license to use embedded pre-existing IP within the deliverables",
        "confidentiality_term_years": 5,
        "governing_law": "State of Illinois (excluding conflicts of law)",
        "dispute_resolution": "Binding arbitration administered by JAMS in Chicago, Illinois under the JAMS Comprehensive Rules",
        "assignment_allowed": False,
    }

    doc = _doc(pdf_path)
    story = [
        Paragraph(expected["agreement_title"], title_style),
        Paragraph(
            f"This Master Services Agreement (the &quot;Agreement&quot;) is entered into as of "
            f"{expected['effective_date']} (the &quot;Effective Date&quot;) by and between "
            f"{expected['customer_name']}, a {expected['customer_state_of_formation']} corporation, "
            f"located at {expected['customer_address']} (&quot;Customer&quot;), and "
            f"{expected['provider_name']}, a {expected['provider_state_of_formation']} limited liability company, "
            f"located at {expected['provider_address']} (&quot;Provider&quot;).",
            body,
        ),
        Paragraph(f"Last counter-signed on {expected['execution_date']}.", body),
        Spacer(1, 10),
        Paragraph("1. Term", h2),
        Paragraph(
            f"The initial term of this Agreement is {expected['initial_term']} from the Effective Date "
            f"(the &quot;Initial Term&quot;). Thereafter, this Agreement will renew for {expected['renewal_term']}. "
            f"Either party may terminate for convenience upon {expected['termination_for_convenience_notice_days']} days' prior written notice.",
            body,
        ),
        Paragraph("2. Fees and Payment", h2),
        Paragraph(
            f"Customer will pay all undisputed fees on {expected['payment_terms']}. Late payments accrue interest at "
            f"{expected['late_fee_rate']}.",
            body,
        ),
        Paragraph("3. Limitation of Liability", h2),
        Paragraph(
            f"{expected['liability_cap']}. Except for breach of confidentiality, indemnification obligations, "
            f"or willful misconduct, neither party will be liable for any indirect, incidental, special, or "
            f"consequential damages.",
            body,
        ),
        Paragraph("4. Indemnification", h2),
        Paragraph(expected["indemnification_summary"], body),
        Paragraph("5. Intellectual Property", h2),
        Paragraph(expected["ip_ownership"], body),
        Paragraph("6. Confidentiality", h2),
        Paragraph(
            f"Each party will protect the other's confidential information using at least reasonable care, "
            f"and these obligations will survive termination for {expected['confidentiality_term_years']} years.",
            body,
        ),
        Paragraph("7. Governing Law and Dispute Resolution", h2),
        Paragraph(
            f"This Agreement is governed by the laws of the {expected['governing_law']}. "
            f"Any dispute arising under this Agreement shall be resolved by {expected['dispute_resolution']}.",
            body,
        ),
        Paragraph("8. Assignment", h2),
        Paragraph(
            "Neither party may assign this Agreement without the other party's prior written consent, "
            "except to an affiliate or in connection with a change of control.",
            body,
        ),
    ]
    doc.build(story)
    (out / "expected.json").write_text(json.dumps(expected, indent=2))
    print(f"  msa -> {pdf_path}")


def main():
    print("Building corpus...")
    build_invoice()
    build_bol()
    build_k1()
    build_lease()
    build_msa()
    print("Done.")


if __name__ == "__main__":
    main()
