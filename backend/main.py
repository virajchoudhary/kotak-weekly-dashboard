import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

import weekly_engine
from db import init_db

app = FastAPI(title="Weekly AMFI Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/")
def read_root():
    return RedirectResponse(url="/docs")


@app.get("/dashboard-data")
@app.get("/api/metrics")
def get_metrics(
    financial_year: str = Query(None),
    fy: str = Query(None),
    period_key: str = Query(None),
):
    try:
        target_fy = financial_year or fy or weekly_engine.latest_financial_year()
        return weekly_engine.dashboard_payload(fy=target_fy, period_key=period_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal dashboard error: {str(exc)}")


@app.post("/upload")
@app.post("/api/upload")
async def upload(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a valid Excel spreadsheet.")
    try:
        uploaded = await file.read()
        period, warnings = weekly_engine.process_upload(uploaded, file.filename)
        return weekly_engine.dashboard_payload(
            fy=period["financialYear"],
            warnings=warnings,
            upload_period=period,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database or processing error: {str(exc)}")


@app.get("/api/archives")
def get_archives():
    try:
        return weekly_engine.list_archives()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/template-status")
def template_status(financial_year: str = Query(None), fy: str = Query(None)):
    target_fy = financial_year or fy or weekly_engine.latest_financial_year()
    periods = weekly_engine.periods_for_fy(target_fy) if target_fy else []
    return {
        "templateVersion": weekly_engine.WORKBOOK_TEMPLATE_VERSION,
        "financialYear": target_fy,
        "monthlyBlockCount": len(periods),
        "hasFinalYtdSummaryBlock": True,
        "monthBlockHeadings": ["AUM", "Gross Sales", "Net Sales"],
        "finalBlockHeadings": ["AUM", "YTD Gross Sales", "YTD Net Sales"],
    }


@app.get("/api/download-summary")
def download_summary(financial_year: str = Query(None), fy: str = Query(None), period_key: str = Query(None)):
    try:
        target_fy = financial_year or fy
        excel_bytes = weekly_engine.compile_summary_workbook(period_key=period_key, fy=target_fy)
        period = weekly_engine.resolve_period(period_key=period_key, fy=target_fy)
        filename = f"AMFI Weekly Summary - {period['period_label'].replace(' ', '_')}.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/download-mom")
def download_mom(financial_year: str = Query(None), fy: str = Query(None)):
    target_fy = financial_year or fy
    try:
        excel_bytes = weekly_engine.compile_mom_workbook(fy=target_fy)
        fy_label = target_fy or weekly_engine.latest_financial_year() or "latest"
        filename = f"AMFI Weekly MoM YTD - FY {fy_label}.xlsx"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/download")
def download_default(financial_year: str = Query(None), fy: str = Query(None)):
    return download_mom(financial_year=financial_year, fy=fy)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
