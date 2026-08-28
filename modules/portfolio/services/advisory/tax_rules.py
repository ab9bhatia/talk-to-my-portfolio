"""Versioned tax/settlement safety records from authoritative sources.

These records drive planning warnings only.  ``effective_from`` is the date the
rule entered this advisor dataset; it is not a claim about when the statute was
originally enacted.
"""

from __future__ import annotations

from dataclasses import dataclass

from modules.portfolio.services.advisory.models import TaxRuleReference


DATASET_EFFECTIVE_FROM = "2026-08-28"
LAST_REVIEWED = "2026-08-28"


@dataclass(frozen=True)
class TaxRule:
    rule_id: str
    jurisdiction: str
    reference: str
    source: str
    source_url: str
    required_inputs: tuple[str, ...]
    planning_note: str
    effective_from: str = DATASET_EFFECTIVE_FROM
    effective_to: str | None = None

    def public_reference(self) -> TaxRuleReference:
        return TaxRuleReference(
            rule_id=self.rule_id,
            jurisdiction=self.jurisdiction,
            reference=self.reference,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            source=self.source,
            source_url=self.source_url,
            last_reviewed=LAST_REVIEWED,
            required_inputs=list(self.required_inputs),
        )


RULES: dict[str, TaxRule] = {
    "INDIA_CAPITAL_GAIN_LOTS": TaxRule(
        rule_id="INDIA_CAPITAL_GAIN_LOTS",
        jurisdiction="IN",
        reference="Income-tax Act capital-gains computation and Section 74 loss treatment",
        source="Income Tax Department, Government of India",
        source_url="https://www.incometaxindia.gov.in/en/sale-of-shares",
        required_inputs=(
            "instrument tax classification",
            "FIFO acquisition dates and quantities",
            "sale date and proceeds",
            "STT and transaction charges",
            "current-year and carried-forward gain/loss ledger",
        ),
        planning_note=(
            "Do not compute gain category, loss set-off, or harvesting benefit from broker "
            "average-price P&L alone."
        ),
    ),
    "INDIA_NRI_WITHHOLDING": TaxRule(
        rule_id="INDIA_NRI_WITHHOLDING",
        jurisdiction="IN",
        reference="Income-tax Act Section 195 / non-resident capital-gains withholding",
        source="Income Tax Department, Government of India",
        source_url=(
            "https://incometaxindia.gov.in/Booklets%20%20Pamphlets/"
            "07.-TDS-on-Payments-to-Non-residents-and-Lower-Nil-Deduction-Certificate-"
            "Sections-195-and-197.pdf"
        ),
        required_inputs=(
            "tax residency and treaty position",
            "instrument and gain classification",
            "broker withholding statement",
            "other Indian taxable income",
        ),
        planning_note=(
            "Treat withholding as a payment/collection mechanism, not proof of final liability."
        ),
    ),
    "RBI_NRI_SETTLEMENT": TaxRule(
        rule_id="RBI_NRI_SETTLEMENT",
        jurisdiction="IN-FEMA",
        reference="FEMA Non-Debt Instruments Rules, NRI/OCI repatriation basis",
        source="Reserve Bank of India",
        source_url="https://rbi.org.in/SCRIPTS/BS_FemaNotifications.aspx?Id=11723",
        required_inputs=(
            "NRO non-repatriable versus NRE/PIS classification",
            "designated bank and settlement account",
            "source of acquisition funds",
            "tax and repatriation documentation",
        ),
        planning_note=(
            "Keep proceeds tied to the originating account until settlement and repatriation "
            "eligibility are confirmed."
        ),
    ),
    "IFSCA_PRODUCT_TAX_EVIDENCE": TaxRule(
        rule_id="IFSCA_PRODUCT_TAX_EVIDENCE",
        jurisdiction="IN-IFSC",
        reference="IFSCA Fund Management Regulations and scheme disclosure requirements",
        source="International Financial Services Centres Authority",
        source_url=(
            "https://ifsca.gov.in/CommonDirect/ViewFile?fileName="
            "IFSCA__Fund_Management__Regulations__2025__Amended_up_to_July_30__2025__"
            "20250818_0105.pdf&id=21626bde60601ef44a0ed022017f9e07"
        ),
        required_inputs=(
            "exact legal product and share class",
            "current offer/placement document",
            "investor eligibility",
            "dated product-specific tax note",
        ),
        planning_note=(
            "Never infer investor-level zero tax from an IFSC or GIFT label alone."
        ),
    ),
    "US_SITUS_ESTATE_REVIEW": TaxRule(
        rule_id="US_SITUS_ESTATE_REVIEW",
        jurisdiction="US",
        reference="Estate tax for nonresidents not citizens; U.S.-situated property",
        source="Internal Revenue Service",
        source_url=(
            "https://www.irs.gov/businesses/small-businesses-self-employed/"
            "estate-tax-for-nonresidents-not-citizens-of-the-united-states"
        ),
        required_inputs=(
            "citizenship and estate-tax domicile",
            "security issuer/fund domicile",
            "U.S.-situated asset value",
            "applicable estate-tax treaty",
        ),
        planning_note=(
            "Track U.S.-situs estate exposure separately from capital gains and dividend "
            "withholding."
        ),
    ),
}


def rule(rule_id: str) -> TaxRule:
    return RULES[rule_id]
