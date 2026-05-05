"""
processing.py  —  Owner: Member C

Responsible for everything downstream of parsing:
  * building df_books
  * before-sort / after-sort 10-row CSV outputs
  * Step 4 features (IsExpensive, NumberOfAuthors)
  * saving books_processed.{csv,json} and books_processed_preview.csv
  * Step 5 summary statistics -> books_summary.csv
  * the consolidated PDF report (names/IDs + 4 tables)
  * final ZIP packaging (ex1p_<ID>.zip)

Public API (orchestrator only calls this):
    run_all(records: list[dict]) -> None

Spec rules to enforce here:
  * books_raw.json and books_processed.json MUST be nested as
    {"records": {"record": [ {"id": "1", "url": "...", ...}, ... ]}}.
  * Per-record missing-field omission must carry through to the JSON
    (a record's dict already omits absent keys — preserve that on write).
  * Stats columns: PriceUSD, Year, StarRating, NumberOfReviews, NumberOfAuthors.
    Treat StarRating == "None" as NaN for stats.
  * Summary table must include the total number of rows.
  * PDF is named consolidated_report.pdf and lives at the project root (also in ZIP root).
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Fill these in before building the report. Members commit names/IDs together.
STUDENT_NAMES_AND_IDS: list[tuple[str, str]] = [
    ("Daniel Segal", "316368240"),
    ("Tal Raiter", "208997908"),
    ("Tomer Kadosh", "209460005"),
    ("Moshe Ohana", "315742692"),
]

# Whichever member's ID the team picks for the ZIP filename.
SUBMITTING_STUDENT_ID: str = "208997908"

_NUMERIC_COLS = ["PriceNIS", "PriceUSD", "Year", "NumberOfReviews", "Weight"]


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    """Build df_books from the parsed records.

    Cast numeric columns to numeric dtypes (PriceNIS, PriceUSD, Year,
    NumberOfReviews, Weight, ...). Be careful with StarRating: it can be the
    string "None" -> keep that as-is; coerce only the rows that are numeric.
    """
    df = pd.DataFrame(records)

    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "StarRating" in df.columns:
        mask = df["StarRating"] != "None"
        df.loc[mask, "StarRating"] = pd.to_numeric(
            df.loc[mask, "StarRating"], errors="coerce"
        )

    return df


def _df_to_json_records(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame rows to dicts, adding id/url, dropping NaN/None values."""
    result = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        rec: dict = {"id": str(i)}
        url = row.get("book_url", None)
        if url and isinstance(url, str):
            rec["url"] = url
        for k, v in row.to_dict().items():
            if k == "book_url":
                continue
            if v is None:
                continue
            try:
                if math.isnan(float(v)):
                    continue
            except (TypeError, ValueError):
                pass
            rec[k] = v
        result.append(rec)
    return result


def save_raw(df: pd.DataFrame) -> None:
    """Save the raw DataFrame (Step 2): books_raw.csv and books_raw.json."""
    df.to_csv("output/books_raw.csv", index=False)

    records = _df_to_json_records(df)
    payload = {"records": {"record": records}}
    with open("output/books_raw.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_before_sort(df: pd.DataFrame) -> None:
    """Print and save df.head(10) -> output/books_before_sort.csv (Step 3)."""
    preview = df.head(10)
    print(preview.to_string())
    preview.to_csv("output/books_before_sort.csv", index=False)


def save_after_sort(df: pd.DataFrame) -> pd.DataFrame:
    """Sort df by Title ascending. Save sorted df.head(10) ->
    output/books_after_sort.csv. Return the sorted DataFrame (Step 3).
    """
    df_sorted = df.sort_values("Title", ascending=True).reset_index(drop=True)
    df_sorted.head(10).to_csv("output/books_after_sort.csv", index=False)
    return df_sorted


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Step 4 — add two derived columns:
      * IsExpensive       : 1 if PriceNIS > median(PriceNIS) else 0
      * NumberOfAuthors   : count of comma-separated authors in the Authors field
    Return the augmented DataFrame.
    """
    df = df.copy()
    df["IsExpensive"] = (df["PriceNIS"] > df["PriceNIS"].median()).astype(int)
    df["NumberOfAuthors"] = df["Authors"].fillna("").apply(
        lambda x: len([a for a in x.split(",") if a.strip()])
    )
    return df


def save_processed(df: pd.DataFrame) -> None:
    """Write the three Step-4 artifacts:
      * output/books_processed.csv
      * output/books_processed.json    -> {"records": {"record": [ {"id":..., "url":..., ...} ]}}
            Each record dict must omit keys whose value is missing (NaN / None).
      * output/books_processed_preview.csv  -> df.head(10)
    """
    df.to_csv("output/books_processed.csv", index=False)
    df.head(10).to_csv("output/books_processed_preview.csv", index=False)

    records = _df_to_json_records(df)
    payload = {"records": {"record": records}}
    with open("output/books_processed.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def compute_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Step 5 — compute mean/std/min/max/median for:
        PriceUSD, Year, StarRating, NumberOfReviews, NumberOfAuthors.
    Treat StarRating == "None" as NaN for the calculation.
    Append a row carrying the total number of rows in df.
    Save -> output/books_summary.csv. Return the summary DataFrame.
    """
    df_stat = df.copy()
    if "StarRating" in df_stat.columns:
        df_stat["StarRating"] = pd.to_numeric(df_stat["StarRating"], errors="coerce")

    cols = [
        c for c in ["PriceUSD", "Year", "StarRating", "NumberOfReviews", "NumberOfAuthors"]
        if c in df_stat.columns
    ]
    summary = df_stat[cols].agg(["mean", "std", "min", "max", "median"])

    total_row = {c: float("nan") for c in cols}
    total_row[cols[0]] = len(df)
    summary.loc["total_rows"] = total_row

    summary.to_csv("output/books_summary.csv")
    return summary


def _df_to_table_data(df: pd.DataFrame) -> list[list]:
    headers = list(df.columns)
    rows = df.astype(str).values.tolist()
    return [headers] + rows


def _make_table(data: list[list]) -> Table:
    t = Table(data, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003087")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return t


def build_report_pdf(
    before_sort: pd.DataFrame,
    after_sort: pd.DataFrame,
    processed_preview: pd.DataFrame,
    summary: pd.DataFrame,
    out_path: str = "consolidated_report.pdf",
) -> None:
    """Build the consolidated PDF report. Must contain:
      * student names + IDs of all group members (from STUDENT_NAMES_AND_IDS)
      * the three 10-row preview tables (before-sort, after-sort, processed-preview)
        with clear references to the corresponding output/ files
      * the summary statistics table
    """
    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=30, rightMargin=30, topMargin=40, bottomMargin=40
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("HW1 — Bookdelivery.com Crawler Report", styles["Title"]))
    story.append(Paragraph("Course 67978: A Needle in a Data Haystack", styles["Normal"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Group Members", styles["Heading2"]))
    member_data = [["Full Name", "Student ID"]]
    for name, sid in STUDENT_NAMES_AND_IDS:
        member_data.append([name, sid])
    if not STUDENT_NAMES_AND_IDS:
        member_data.append(["(not filled in)", ""])
    story.append(_make_table(member_data))
    story.append(Spacer(1, 16))

    sections = [
        ("Books Before Sort — first 10 rows (output/books_before_sort.csv)", before_sort),
        ("Books After Sort by Title — first 10 rows (output/books_after_sort.csv)", after_sort),
        ("Processed Data Preview — first 10 rows (output/books_processed_preview.csv)", processed_preview),
        ("Summary Statistics (output/books_summary.csv)", summary),
    ]
    for title, df in sections:
        story.append(Paragraph(title, styles["Heading2"]))
        display_df = df.reset_index() if (df.index.name or list(df.index) != list(range(len(df)))) else df
        story.append(_make_table(_df_to_table_data(display_df)))
        story.append(Spacer(1, 16))

    doc.build(story)


def build_zip(student_id: str = "") -> None:
    """Assemble ex1p_<ID>.zip with this folder structure:

        ex1p_<ID>.zip
        ├── consolidated_report.pdf
        ├── code/
        │   ├── books_crawler.py
        │   ├── crawler.py
        │   ├── parser.py
        │   ├── processing.py
        │   └── requirements.txt
        └── output/
            ├── books_raw.csv
            ├── books_raw.json
            ├── books_example.json       (if present)
            ├── books_example.jpg        (if present)
            ├── books_before_sort.csv
            ├── books_after_sort.csv
            ├── books_processed.csv
            ├── books_processed.json
            ├── books_processed_preview.csv
            └── books_summary.csv
    """
    zip_name = f"ex1p_{student_id}.zip"

    src_dir = Path("src")
    out_dir = Path("output")
    root = Path(".")

    code_files = ["books_crawler.py", "crawler.py", "parser.py", "processing.py"]
    output_files = [
        "books_raw.csv",
        "books_raw.json",
        "books_example.json",
        "books_example.jpg",
        "books_before_sort.csv",
        "books_after_sort.csv",
        "books_processed.csv",
        "books_processed.json",
        "books_processed_preview.csv",
        "books_summary.csv",
    ]

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        # consolidated_report.pdf at ZIP root
        report = root / "consolidated_report.pdf"
        if report.exists():
            zf.write(report, "consolidated_report.pdf")

        for fname in code_files:
            path = src_dir / fname
            if path.exists():
                zf.write(path, f"code/{fname}")

        req_path = root / "requirements.txt"
        if req_path.exists():
            zf.write(req_path, "code/requirements.txt")

        for fname in output_files:
            path = out_dir / fname
            if path.exists():
                zf.write(path, f"output/{fname}")


def run_all(records: list[dict]) -> None:
    """Convenience entry point used by books_crawler.main().

    Calls every step in order:
        df = build_dataframe(records)
        save_raw(df)                          <- Step 2: books_raw.csv / .json
        save_before_sort(df)                  <- Step 3: before sort preview
        df_sorted = save_after_sort(df)       <- Step 3: after sort preview
        df_proc = add_features(df_sorted)     <- Step 4: IsExpensive, NumberOfAuthors
        save_processed(df_proc)               <- Step 4: processed CSV/JSON
        summary = compute_summary(df_proc)    <- Step 5: summary stats
        build_report_pdf(...)                 <- consolidated_report.pdf
        build_zip(SUBMITTING_STUDENT_ID)      <- ex1p_<ID>.zip
    """
    Path("output").mkdir(exist_ok=True)

    df = build_dataframe(records)
    save_raw(df)
    save_before_sort(df)
    df_sorted = save_after_sort(df)
    df_proc = add_features(df_sorted)
    save_processed(df_proc)
    summary = compute_summary(df_proc)
    build_report_pdf(df.head(10), df_sorted.head(10), df_proc.head(10), summary)
    build_zip(SUBMITTING_STUDENT_ID)