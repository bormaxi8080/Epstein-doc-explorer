"""Generate local operator configs/commands; never execute downloads or inference."""

import json
from dataclasses import asdict
from pathlib import Path

from .archive_transfer import Options
from .corpus_runtime import RunConfig
from .distribution_contracts import load_release, load_workspace, safe_path
from .distribution_install import verify_install
from .state import atomic_json


def prepare_workflow(
    workspace, installation, model, routing_revision, base_url="http://127.0.0.1:8082"
):
    config, storage = load_workspace(workspace)
    installation = Path(installation).absolute()
    verify_install(installation, reserve_bytes=storage.reserve_bytes)
    release = load_release(installation / "release.json")
    receipt = json.loads((installation / "install.json").read_text())
    included = set(receipt["artifact_ids"])
    items = {
        a["role"]: a
        for a in release["artifacts"]
        if a["id"] in included
        and a["role"] in ("inventory-manifest", "inventory-full", "inventory-db")
    }
    source = items.get("inventory-manifest") or items.get("inventory-full")
    if not source:
        raise ValueError("Install a pipeline or inventory-full profile first")
    roots = {k: storage.path(v) for k, v in config["paths"].items()}
    if not all(p.is_dir() for p in roots.values()):
        raise ValueError("Initialize workspace roles first")
    corpus = RunConfig(
        str(roots["corpus"]),
        model,
        routing_revision,
        base_url=base_url,
        max_calls_per_run=100,
        max_calls_total=1000,
        minimum_call_interval=5,
        limits={"reserve_bytes": storage.reserve_bytes, "worker_memory_mb": 512},
    )
    corpus.fcc()
    configs = storage.mkdir("configs/" + release["release_id"])
    settings = {
        "archive.json": asdict(Options(reserve_bytes=storage.reserve_bytes)),
        "corpus.json": asdict(corpus),
    }
    for name, value in settings.items():
        target = configs / name
        if target.exists() and json.loads(target.read_text()) != value:
            raise ValueError(
                "Existing workflow config differs; choose a new workspace/config release"
            )
    selections = storage.mkdir(config["paths"]["inventory"] + "/selections")
    download = [
        "explorer-download",
        "--destination",
        str(roots["downloads"]),
        "--config",
        str(configs / "archive.json"),
    ]
    process = ["explorer-corpus", "--config", str(configs / "corpus.json")]
    planning = download + [
        "plan",
        "--manifest" if source["role"] == "inventory-manifest" else "--dump",
        str(safe_path(installation, source["install_path"])),
        "--layout",
        "logical",
    ]
    if items.get("inventory-db"):
        planning += [
            "--evidence-db",
            str(safe_path(installation, items["inventory-db"]["install_path"])),
        ]
    commands = {
        "plan": planning,
        "pilot_select": download + ["pilot", "--first", "250"],
        "pilot_fetch": download + ["fetch", "--pilot"],
        "pilot_verify": download + ["verify", "--pilot"],
        "full_fetch": download + ["fetch"],
        "full_verify": download + ["verify"],
        "init_corpus": process + ["init"],
        "offline_work": process + ["work", "--limit", "500"],
        "fcc_preflight": process + ["preflight"],
        "model_work": process + ["work", "--limit", "100", "--allow-fcc"],
        "private_model_work": process + ["work", "--limit", "100", "--allow-fcc", "--allow-review"],
        "status": process + ["status"],
    }
    for scope in ("pilot", "full"):
        selection = selections / scope
        commands[scope + "_freeze"] = process + [
            "freeze",
            str(roots["downloads"]),
            str(selection),
            "--scope",
            scope,
        ]
        commands[scope + "_import"] = process + ["import", str(selection / "selection.json")]
    commands["plan_corpus"] = process + ["plan"]
    for name, value in settings.items():
        atomic_json(configs / name, value)
    atomic_json(configs / "commands.json", commands)
    return dict(
        config_directory=str(configs),
        commands=commands,
        executed_work=False,
        network_or_model_calls=False,
    )
