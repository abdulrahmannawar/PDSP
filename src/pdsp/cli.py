from __future__ import annotations
import typer
from rich.console import Console

console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=True)

query_app = typer.Typer(help="Query subcommands")
app.add_typer(query_app, name="query")


@app.command(help="Process PDFs in a directory and build the SQLite dataset")
def process(
    pdf_dir: str = typer.Argument(..., help="Path to directory containing PDFs"),
    db: str = typer.Option("products.sqlite", "--db", help="SQLite database file"),
    jsonl: str | None = typer.Option("products.jsonl", "--jsonl", help="Optional JSONL export"),
):
    console.print("[yellow]process: not implemented yet[/yellow]")
    console.print(f"pdf_dir={pdf_dir}, db={db}, jsonl={jsonl}")


@query_app.command("by-model")
def by_model(
    db: str = typer.Option("products.sqlite", "--db"),
    model: str = typer.Option(..., "--model", "-m"),
):
    console.print("[yellow]query by-model: not implemented yet[/yellow]")
    console.print(f"db={db}, model={model}")


@query_app.command("by-brand")
def by_brand(
    db: str = typer.Option("products.sqlite", "--db"),
    brand: str = typer.Option(..., "--brand", "-b"),
):
    console.print("[yellow]query by-brand: not implemented yet[/yellow]")
    console.print(f"db={db}, brand={brand}")


@query_app.command("by-spec")
def by_spec(
    db: str = typer.Option("products.sqlite", "--db"),
    key: str = typer.Option(..., "--key"),
    op: str = typer.Option(">=", "--op", help="One of =, !=, <, <=, >, >="),
    value: float = typer.Option(..., "--value"),
    unit: str | None = typer.Option(None, "--unit"),
):
    console.print("[yellow]query by-spec: not implemented yet[/yellow]")
    console.print(f"db={db}, key={key}, op={op}, value={value}, unit={unit}")
