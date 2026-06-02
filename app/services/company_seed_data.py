from __future__ import annotations

from app.models.company import CompanyCreate, CompanyStatus
from app.models.source_registry import SourceRegistryCreate

MVP_SEED_COMPANIES: list[CompanyCreate] = [
    CompanyCreate(
        company_id="reliance_industries",
        name="Reliance Industries Ltd",
        display_name="Reliance Industries",
        nse_symbol="RELIANCE",
        bse_scrip_code="500325",
        isin="INE002A01018",
        sector="Energy",
        industry="Oil & Gas",
        market_index="NIFTY50",
        priority_rank=1,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    ),
    CompanyCreate(
        company_id="hdfc_bank",
        name="HDFC Bank Ltd",
        display_name="HDFC Bank",
        nse_symbol="HDFCBANK",
        bse_scrip_code="500180",
        isin="INE040A01034",
        sector="Financial Services",
        industry="Banks",
        market_index="NIFTY50",
        priority_rank=2,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    ),
    CompanyCreate(
        company_id="tata_motors",
        name="Tata Motors Ltd",
        display_name="Tata Motors",
        nse_symbol="TATAMOTORS",
        bse_scrip_code="500570",
        isin="INE155A01022",
        sector="Consumer Discretionary",
        industry="Automobiles",
        market_index="NIFTY50",
        priority_rank=3,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    ),
    CompanyCreate(
        company_id="itc",
        name="ITC Ltd",
        display_name="ITC",
        nse_symbol="ITC",
        bse_scrip_code="500875",
        isin="INE154A01025",
        sector="Fast Moving Consumer Goods",
        industry="Diversified FMCG",
        market_index="NIFTY50",
        priority_rank=4,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    ),
    CompanyCreate(
        company_id="infosys",
        name="Infosys Ltd",
        display_name="Infosys",
        nse_symbol="INFY",
        bse_scrip_code="500209",
        isin="INE009A01021",
        sector="Information Technology",
        industry="IT Services",
        market_index="NIFTY50",
        priority_rank=5,
        is_seed_company=True,
        status=CompanyStatus.ACTIVE,
    ),
]


def seed_source_registry_entries(company_id: str) -> list[SourceRegistryCreate]:
    """Source registry entries are admin-managed; MVP seed does not create placeholder official URLs."""
    del company_id
    return []
