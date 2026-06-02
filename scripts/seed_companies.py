"""Seed MVP companies only. Source registry URLs are admin-managed per T-012."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.db.mongodb import MongoClientManager
from app.repositories.company_repo import CompanyRepository
from app.repositories.source_registry_repo import SourceRegistryRepository
from app.services.company_service import CompanyService


async def run_seed(*, dry_run: bool) -> int:
    settings = get_settings()
    mongo = MongoClientManager(settings)
    await mongo.connect()
    try:
        company_repo = CompanyRepository(mongo, settings)
        source_repo = SourceRegistryRepository(mongo, settings)
        service = CompanyService(company_repo, source_repo)
        if dry_run:
            from app.services.company_seed_data import MVP_SEED_COMPANIES

            print(f"Dry run: would seed {len(MVP_SEED_COMPANIES)} companies (no source registry URLs).")
            for company in MVP_SEED_COMPANIES:
                print(f"  - {company.company_id} ({company.nse_symbol})")
            return 0
        await company_repo.ensure_indexes()
        await source_repo.ensure_indexes()
        seeded = await service.seed_mvp_companies(include_sources=False)
        print(f"Seeded {len(seeded)} companies (no source registry URLs).")
        return 0
    finally:
        await mongo.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed FinSight MVP companies.")
    parser.add_argument("--dry-run", action="store_true", help="Print seed plan without writing.")
    args = parser.parse_args()
    return asyncio.run(run_seed(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
