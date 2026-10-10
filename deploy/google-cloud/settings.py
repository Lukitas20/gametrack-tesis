"""Actualiza parámetros no secretos conservando las etapas anteriores."""
import argparse
import json
from pathlib import Path
import re


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project")
    parser.add_argument("--billing-account")
    parser.add_argument("--region")
    parser.add_argument("--image")
    parser.add_argument("--model-version")
    parser.add_argument("--training-version")
    parser.add_argument("--data-label", choices=("demo", "observed"))
    for field in ("jobs", "web", "scheduler"):
        parser.add_argument(f"--enable-{field}", action="store_const", const=True, default=None)
    args = parser.parse_args(argv)
    destination = Path(__file__).with_name("settings.auto.tfvars.json")
    values = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
    if args.project and values.get("project_id") not in (None, args.project):
        parser.error("Esta carpeta ya corresponde a otro proyecto; usar una copia distinta.")
    names = {"project": "project_id", "billing_account": "billing_account_id",
             "data_label": "model_data_label", "enable_jobs": "deploy_jobs",
             "enable_web": "deploy_web", "enable_scheduler": "enable_scheduler"}
    for field, value in vars(args).items():
        if value is not None:
            values[names.get(field, field)] = value
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", values.get("project_id", "")):
        parser.error("Indicar --project con el ID real.")
    if values.get("deploy_web") and not values.get("deploy_jobs"):
        parser.error("Preparar jobs y migración antes de publicar.")
    if values.get("enable_scheduler") and not values.get("deploy_web"):
        parser.error("Verificar la web antes de habilitar las tandas automáticas.")
    destination.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    print("Parámetros guardados; las etapas ya habilitadas se conservaron.")


if __name__ == "__main__":
    main()
