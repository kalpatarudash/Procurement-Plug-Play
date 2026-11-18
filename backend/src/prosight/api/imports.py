"""Excel/CSV templates, upload validation, import, and restoring the demo data."""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import importer, runtime, seed
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import Setting

router = APIRouter(tags=["data import"])
Db = Annotated[Session, Depends(get_db)]
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class IssueOut(BaseModel):
    sheet: str
    row: int | None
    column: str | None
    message: str


class ReportOut(BaseModel):
    ok: bool
    imported: bool = False
    errors: list[IssueOut]
    error_count: int
    warnings: list[str]
    summary: dict[str, Any]


class DatasetOut(BaseModel):
    source: str  # demo | import
    as_of: str
    currency: str
    fiscal_year_start_month: int


def _report_out(report: importer.Report, imported: bool = False) -> ReportOut:
    return ReportOut(ok=report.ok, imported=imported, errors=[IssueOut(**asdict(e)) for e in report.errors],
                     error_count=report.error_count, warnings=report.warnings, summary=report.summary)


async def _read(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out, total = [], 0
    for f in files:
        content = await f.read()
        total += len(content)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload is larger than 25 MB.")
        out.append((f.filename or "upload", content))
    if not out:
        raise HTTPException(422, "Choose an .xlsx file or one or more .csv files.")
    return out


@router.get("/import/template.xlsx")
def download_template(db: Db, sample: bool = False):
    """Excel template. sample=true fills it with the current dataset as a complete example."""
    content = importer.build_workbook(importer.template_rows(db, sample), sample)
    name = f"prosight-{'sample-data' if sample else 'template'}.xlsx"
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/import/template.zip")
def download_csv_template(db: Db, sample: bool = False):
    """The same template as one CSV per sheet, zipped."""
    content = importer.build_csv_zip(importer.template_rows(db, sample))
    name = f"prosight-{'sample-data' if sample else 'template'}-csv.zip"
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/import/validate", response_model=ReportOut)
async def validate_upload(files: Annotated[list[UploadFile], File(description="One .xlsx, or .csv files named after the sheets")]):
    """Check an upload without changing anything."""
    return _report_out(importer.validate(await _read(files)))


@router.post("/import", response_model=ReportOut)
async def import_upload(db: Db, files: Annotated[list[UploadFile], File()]):
    """Validate, and if there are no errors, replace the dataset with the upload."""
    report = importer.validate(await _read(files))
    if not report.ok:
        raise HTTPException(422, _report_out(report).model_dump())
    importer.replace_dataset(db, report)
    return _report_out(report, imported=True)


@router.post("/import/reset-demo", response_model=DatasetOut)
def reset_demo():
    """Throw away the current data and restore the synthetic demo dataset."""
    seed.reset_and_seed()
    with SessionLocal() as db:
        runtime.apply(db)
        return dataset_info(db)


@router.get("/dataset", response_model=DatasetOut)
def dataset_info(db: Db):
    return DatasetOut(
        source="import" if db.get(Setting, "as_of") else "demo",
        as_of=settings.as_of.isoformat(),
        currency=settings.currency,
        fiscal_year_start_month=settings.fiscal_year_start_month,
    )
