from __future__ import annotations
import json
from typing import Optional, Iterable
import typer
from rich.console import Console
from rich.table import Table

from pdsp.db import (
    get_connection, ensure_schema, insert_products,
    query_by_model, query_by_brand, query_by_spec,
    query_specs_for_code, audit_spec_coverage, query_by_spec_text,
)
from pdsp.extract import extract_products

console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=True)

query_app = typer.Typer(help="Query subcommands")
app.add_typer(query_app, name="query")


@app.command(help="Process PDFs in a directory and build the SQLite dataset")
def process(
    pdf_dir: str = typer.Argument(..., help="Path to directory containing PDFs"),
    db: str = typer.Option("products.sqlite", "--db", help="SQLite database file"),
    jsonl: Optional[str] = typer.Option("products.jsonl", "--jsonl", help="Optional JSONL export"),
):
    # 1) extract
    products = extract_products(pdf_dir)
    if not products:
        console.print("[yellow]No PDFs found or directory empty.[/yellow]")

    # 2) store
    conn = get_connection(db)
    ensure_schema(conn)
    inserted_ids = insert_products(conn, products)

    # 3) optional JSONL export
    if jsonl is not None:
        with open(jsonl, "w", encoding="utf-8") as f:
            for p in products:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # 4) report
    console.print(f"[bold green]Processed[/bold green] {len(inserted_ids)} products → {db}")
    if jsonl is not None:
        console.print(f"[bold cyan]Export[/bold cyan] → {jsonl}")


@app.command(help="Inspect all specs for a given ordering code or model")
def inspect(
    code: str = typer.Option(..., "--code", "-c", help="ordering_code or model_no"),
    db: str = typer.Option("products.sqlite", "--db"),
):
    conn = get_connection(db)
    rows = query_specs_for_code(conn, code)
    if not rows:
        console.print("[yellow]No specs found for this code.[/yellow]")
        raise typer.Exit(code=0)

    t = Table(title=f"Specs for {code}")
    t.add_column("product_id"); t.add_column("spec_key"); t.add_column("spec_value_num"); t.add_column("spec_value_text"); t.add_column("unit"); t.add_column("raw")
    for r in rows:
        t.add_row(str(r["product_id"]), r["spec_key"], str(r["spec_value_num"]), str(r["spec_value_text"]), str(r.get("unit") or ""), str(r.get("raw") or ""))
    console.print(t)


@app.command(help="Report coverage per spec_key (how many rows; how many numeric)")
def audit(
    db: str = typer.Option("products.sqlite", "--db"),
):
    conn = get_connection(db)
    rows = audit_spec_coverage(conn)
    if not rows:
        console.print("[yellow]No specs in database.[/yellow]")
        raise typer.Exit(code=0)

    t = Table(title="Spec coverage")
    for col in ["spec_key", "total_rows", "numeric_rows"]:
        t.add_column(col)
    for r in rows:
        t.add_row(r["spec_key"], str(r["total_rows"]), str(r["numeric_rows"]))
    console.print(t)


@query_app.command("by-model")
def by_model(
    db: str = typer.Option("products.sqlite", "--db"),
    model: str = typer.Option(..., "--model", "-m"),
):
    conn = get_connection(db)
    rows = query_by_model(conn, model)
    _print_products(rows)


@query_app.command("by-brand")
def by_brand(
    db: str = typer.Option("products.sqlite", "--db"),
    brand: str = typer.Option(..., "--brand", "-b"),
):
    conn = get_connection(db)
    rows = query_by_brand(conn, brand)
    _print_products(rows)


@query_app.command("by-spec")
def by_spec(
    db: str = typer.Option("products.sqlite", "--db"),
    key: str = typer.Option(..., "--key"),
    op: str = typer.Option(">=", "--op", help="One of =, !=, <, <=, >, >="),
    value: float = typer.Option(..., "--value"),
    unit: Optional[str] = typer.Option(None, "--unit"),
):
    conn = get_connection(db)
    rows = query_by_spec(conn, key, op, value)
    _print_products_with_spec(rows)


@query_app.command("by-spec-text")
def by_spec_text(
    db: str = typer.Option("products.sqlite", "--db"),
    key: str = typer.Option(..., "--key"),
    contains: Optional[str] = typer.Option(None, "--contains", help="substring match"),
    equals: Optional[str] = typer.Option(None, "--equals", help="case-insensitive exact"),
):
    conn = get_connection(db)
    rows = query_by_spec_text(conn, key, contains=contains, equals=equals)
    _print_products_with_spec(rows)


def _print_products(rows: Iterable):
    rows = list(rows)
    if not rows:
        console.print("[yellow]No results[/yellow]")
        return

    t = Table(title="Products", show_lines=False)
    for col in ["id", "brand", "family", "model_no", "article_number", "ordering_code", "product_name", "source_pdf"]:
        t.add_column(col)

    for r in rows:
        t.add_row(
            str(r.get("id") if isinstance(r, dict) else r["id"]),
            str(r.get("brand") if isinstance(r, dict) else r["brand"]),
            str(r.get("family") if isinstance(r, dict) else r["family"]),
            str(r.get("model_no") if isinstance(r, dict) else r["model_no"]),
            str(r.get("article_number") if isinstance(r, dict) else r["article_number"]),
            str(r.get("ordering_code") if isinstance(r, dict) else r["ordering_code"]),
            str(r.get("product_name") if isinstance(r, dict) else r["product_name"]),
            str(r.get("source_pdf") if isinstance(r, dict) else r["source_pdf"]),
        )
    console.print(t)


def _print_products_with_spec(rows: Iterable):
    rows = list(rows)
    if not rows:
        console.print("[yellow]No results[/yellow]")
        return

    t = Table(title="Products (spec filter)", show_lines=False)
    for col in ["id", "brand", "family", "model_no", "ordering_code", "product_name", "spec_key", "spec_value_num", "spec_value_text", "unit", "source_pdf"]:
        t.add_column(col)

    for r in rows:
        t.add_row(
            str(r["id"]),
            str(r["brand"]),
            str(r["family"]),
            str(r["model_no"]),
            str(r["ordering_code"]),
            str(r["product_name"]),
            str(r["spec_key"]),
            str(r["spec_value_num"]),
            str(r["spec_value_text"]),
            str(r["unit"]),
            str(r["source_pdf"]),
        )
    console.print(t)
