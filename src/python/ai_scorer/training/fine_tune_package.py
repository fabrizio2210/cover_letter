from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from textwrap import dedent

from src.python.ai_scorer.training.fine_tune_manifest import now_epoch, write_manifest


def _run(cmd: list[str], cwd: str | None = None) -> None:
    completed = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        details = "\n".join(part for part in [stdout, stderr] if part)
        if details:
            raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(cmd)}\n{details}")
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(cmd)}")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="")


def _has_torch(python_bin: str) -> bool:
    probe = subprocess.run(
        [python_bin, "-c", "import torch"],
        check=False,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def _resolve_converter_python() -> str:
    candidates: list[str] = [sys.executable]

    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    venv_python = os.path.join(repo_root, ".venv", "bin", "python")
    if os.path.isfile(venv_python):
        candidates.append(venv_python)

    for candidate in candidates:
        if _has_torch(candidate):
            return candidate

    raise RuntimeError(
        "No Python interpreter with torch found for GGUF conversion. "
        "Install torch in your active Python or use the project .venv."
    )


def _convert_to_gguf(merged_dir: str, gguf_path: str, outtype: str, convert_script: str) -> None:
    if not os.path.isfile(convert_script):
        raise RuntimeError(
            "GGUF convert script not found. Provide --convert-script (e.g. llama.cpp/convert_hf_to_gguf.py)."
        )
    converter_python = _resolve_converter_python()
    _run([
        converter_python,
        convert_script,
        merged_dir,
        "--outfile",
        gguf_path,
        "--outtype",
        outtype,
    ])


def _find_llama_quantize(convert_script: str) -> str:
    script_dir = os.path.dirname(os.path.abspath(convert_script))
    candidates = [
        os.path.join(script_dir, "llama-quantize"),
        os.path.join(script_dir, "quantize"),
        os.path.join(script_dir, "build", "bin", "llama-quantize"),
        os.path.join(script_dir, "build", "bin", "quantize"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    which_llama_quantize = shutil.which("llama-quantize")
    if which_llama_quantize:
        return which_llama_quantize
    which_quantize = shutil.which("quantize")
    if which_quantize:
        return which_quantize

    raise RuntimeError(
        "Requested quantization requires llama-quantize, but it was not found. "
        "Build llama.cpp tools or use --quant f16/auto."
    )


def _quantize_gguf(input_gguf: str, output_gguf: str, quant: str, convert_script: str) -> None:
    quantize_bin = _find_llama_quantize(convert_script)
    _run([quantize_bin, input_gguf, output_gguf, quant.upper()])


def _write_modelfile(modelfile_path: str, gguf_filename: str, system_prompt: str) -> None:
    content = dedent(
        f"""
        FROM ./{gguf_filename}
        PARAMETER temperature 0
        SYSTEM \"\"\"{system_prompt}\"\"\"
        """
    ).strip() + "\n"
    with open(modelfile_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _ollama_create(tag: str, modelfile_path: str, working_dir: str) -> None:
    if shutil.which("ollama") is None:
        raise RuntimeError("ollama CLI not found in PATH")
    _run(["ollama", "create", tag, "-f", modelfile_path], cwd=working_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package fine-tuned model to GGUF and Ollama")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--merged-dir", default="", help="Defaults to <run-dir>/merged-hf")
    parser.add_argument("--output-dir", default="", help="Defaults to <run-dir>/packaged")
    parser.add_argument("--convert-script", default=os.environ.get("LLAMA_CPP_CONVERT_SCRIPT", ""))
    parser.add_argument("--quant", default="q4_k_m")
    parser.add_argument("--ollama-tag", required=True)
    parser.add_argument("--system-prompt", default="You are an AI scorer. Return only a score from 0 to 5 or N/A.")
    parser.add_argument("--skip-ollama-create", action="store_true")
    args = parser.parse_args(argv)

    merged_dir = args.merged_dir or os.path.join(args.run_dir, "merged-hf")
    if not os.path.isdir(merged_dir):
        raise SystemExit(f"Merged directory does not exist: {merged_dir}")

    output_dir = args.output_dir or os.path.join(args.run_dir, "packaged")
    os.makedirs(output_dir, exist_ok=True)

    requested_quant = args.quant.strip().lower()
    effective_quant = requested_quant
    gguf_filename = f"model-{requested_quant}.gguf"
    gguf_path = os.path.join(output_dir, gguf_filename)
    try:
        _convert_to_gguf(merged_dir, gguf_path, requested_quant, args.convert_script)
    except RuntimeError as exc:
        message = str(exc)
        unsupported_outtype = "invalid choice" in message and "--outtype" in message
        if not (requested_quant.startswith("q") and unsupported_outtype):
            raise

        # Newer llama.cpp split K-quantization out of convert_hf_to_gguf.py.
        intermediate_gguf = os.path.join(output_dir, "model-f16.gguf")
        try:
            _convert_to_gguf(merged_dir, intermediate_gguf, "f16", args.convert_script)
        except RuntimeError:
            _convert_to_gguf(merged_dir, intermediate_gguf, "auto", args.convert_script)
        try:
            _quantize_gguf(intermediate_gguf, gguf_path, requested_quant, args.convert_script)
        except RuntimeError as quantize_exc:
            quantize_message = str(quantize_exc)
            if "llama-quantize" not in quantize_message and "quantize" not in quantize_message:
                raise
            print(
                "[training.package] WARNING: llama-quantize not found; "
                "falling back to f16 GGUF output."
            )
            effective_quant = "f16"
            gguf_filename = os.path.basename(intermediate_gguf)
            gguf_path = intermediate_gguf

    modelfile_path = os.path.join(output_dir, "Modelfile")
    _write_modelfile(modelfile_path, gguf_filename, args.system_prompt)

    if not args.skip_ollama_create:
        _ollama_create(args.ollama_tag, modelfile_path, output_dir)

    manifest_path = os.path.join(args.run_dir, "package_manifest.json")
    write_manifest(
        manifest_path,
        {
            "run_dir": args.run_dir,
            "merged_dir": merged_dir,
            "output_dir": output_dir,
            "gguf": gguf_path,
            "requested_quant": requested_quant,
            "effective_quant": effective_quant,
            "modelfile": modelfile_path,
            "ollama_tag": args.ollama_tag,
            "ollama_create_executed": not args.skip_ollama_create,
            "completed_at_epoch": now_epoch(),
        },
    )

    print(f"[training.package] gguf={gguf_path}")
    print(f"[training.package] modelfile={modelfile_path}")
    print(f"[training.package] manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
