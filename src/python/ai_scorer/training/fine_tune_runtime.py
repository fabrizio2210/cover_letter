from __future__ import annotations

import argparse
import importlib.util
import json
from dataclasses import asdict, dataclass


@dataclass
class RuntimeInfo:
    selected_path: str
    cuda_available: bool
    xpu_available: bool
    xpu_device: str
    unsloth_available: bool
    trl_available: bool
    transformers_available: bool
    peft_available: bool
    warning: str


def _is_importable(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def detect_runtime() -> RuntimeInfo:
    cuda_available = False
    xpu_available = False
    xpu_device = ""
    torch_available = _is_importable("torch")
    if torch_available:
        import torch  # type: ignore

        cuda_available = bool(torch.cuda.is_available())
        xpu_available = bool(
            hasattr(torch, "xpu") and torch.xpu.is_available()
        )
        if xpu_available:
            xpu_device = str(torch.xpu.get_device_name(0))

    unsloth_available = _is_importable("unsloth")
    trl_available = _is_importable("trl")
    transformers_available = _is_importable("transformers")
    peft_available = _is_importable("peft")

    if cuda_available and transformers_available and peft_available:
        return RuntimeInfo(
            selected_path="cuda-transformers-peft",
            cuda_available=True,
            xpu_available=xpu_available,
            xpu_device=xpu_device,
            unsloth_available=unsloth_available,
            trl_available=trl_available,
            transformers_available=transformers_available,
            peft_available=peft_available,
            warning="",
        )

    if xpu_available and transformers_available and peft_available:
        return RuntimeInfo(
            selected_path="xpu-transformers-peft",
            cuda_available=cuda_available,
            xpu_available=True,
            xpu_device=xpu_device,
            unsloth_available=unsloth_available,
            trl_available=trl_available,
            transformers_available=transformers_available,
            peft_available=peft_available,
            warning="",
        )

    warning = (
        "A CUDA or Intel XPU Transformers + PEFT training runtime is not available. "
        "Falling back to CPU Transformers + PEFT. This can be significantly slower "
        "and may require lower max_steps, shorter sequence length, and smaller batch size."
    )
    return RuntimeInfo(
        selected_path="cpu-transformers-peft",
        cuda_available=cuda_available,
        xpu_available=xpu_available,
        xpu_device=xpu_device,
        unsloth_available=unsloth_available,
        trl_available=trl_available,
        transformers_available=transformers_available,
        peft_available=peft_available,
        warning=warning,
    )


def resolve_torch_device():
    import torch  # type: ignore

    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        return torch.device("xpu")
    return torch.device("cpu")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect CUDA/XPU/CPU fine-tuning runtime path")
    parser.add_argument("--output", default="", help="Optional JSON file for runtime report")
    args = parser.parse_args(argv)

    info = detect_runtime()
    payload = asdict(info)
    rendered = json.dumps(payload, indent=2)
    print(rendered)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")

    if info.warning:
        print(f"[training.runtime] WARNING: {info.warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
