from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


CONTRACT_PATH = Path(__file__).with_name("fine_tune.contract.json")


@dataclass(frozen=True)
class ModelProfile:
    name: str
    hf_id: str
    ollama_tag: str
    revision: str
    loader: str
    torch_dtype: str
    attention_implementation: str
    thinking: bool
    lora_target_modules: tuple[str, ...]

    def manifest_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["lora_target_modules"] = list(self.lora_target_modules)
        return payload


def _profile_from_mapping(
    name: str,
    payload: dict[str, Any],
    *,
    fallback: ModelProfile | None = None,
) -> ModelProfile:
    def value(key: str, default: Any = None) -> Any:
        if key in payload:
            return payload[key]
        if fallback is not None:
            return getattr(fallback, key)
        if default is not None:
            return default
        raise ValueError(f"Model profile {name!r} is missing required field {key!r}")

    targets = value("lora_target_modules")
    if not isinstance(targets, (list, tuple)) or not targets:
        raise ValueError(f"Model profile {name!r} must define LoRA target modules")
    return ModelProfile(
        name=name,
        hf_id=str(value("hf_id")),
        ollama_tag=str(value("ollama_tag")),
        revision=str(value("revision")),
        loader=str(value("loader")),
        torch_dtype=str(value("torch_dtype")),
        attention_implementation=str(value("attention_implementation", "")),
        thinking=bool(value("thinking", False)),
        lora_target_modules=tuple(str(target) for target in targets),
    )


def _load_model_profiles() -> tuple[str, dict[str, ModelProfile]]:
    with CONTRACT_PATH.open("r", encoding="utf-8") as handle:
        contract = json.load(handle)
    default_name = str(contract["default_model_profile"])
    profile_payloads = contract.get("model_profiles") or {}
    profiles = {
        str(name): _profile_from_mapping(str(name), payload)
        for name, payload in profile_payloads.items()
    }
    if default_name not in profiles:
        raise ValueError(
            f"Default model profile {default_name!r} is not defined in {CONTRACT_PATH}"
        )
    return default_name, profiles


DEFAULT_MODEL_PROFILE, MODEL_PROFILES = _load_model_profiles()
MODEL_PROFILE_NAMES = tuple(MODEL_PROFILES)


def resolve_model_profile(
    profile_name: str = DEFAULT_MODEL_PROFILE,
    *,
    hf_id: str = "",
    revision: str = "",
    ollama_tag: str = "",
) -> ModelProfile:
    try:
        profile = MODEL_PROFILES[profile_name]
    except KeyError as exc:
        raise ValueError(f"Unknown model profile: {profile_name}") from exc
    return _override_model_identity(
        profile,
        hf_id=hf_id,
        revision=revision,
        ollama_tag=ollama_tag,
    )


def _override_model_identity(
    profile: ModelProfile,
    *,
    hf_id: str = "",
    revision: str = "",
    ollama_tag: str = "",
) -> ModelProfile:
    resolved_hf_id = hf_id or profile.hf_id
    if revision:
        resolved_revision = revision
    elif hf_id and hf_id != profile.hf_id:
        # A commit belongs to one Hugging Face repository. Retaining the
        # profile's pinned commit after changing repositories is always wrong.
        resolved_revision = "main"
    else:
        resolved_revision = profile.revision
    return replace(
        profile,
        hf_id=resolved_hf_id,
        revision=resolved_revision,
        ollama_tag=ollama_tag or profile.ollama_tag,
    )


def infer_model_profile_name(hf_id: str) -> str:
    for name, profile in MODEL_PROFILES.items():
        if profile.hf_id == hf_id:
            return name
    raise ValueError(
        f"Cannot infer a model profile for {hf_id!r}; pass --model-profile explicitly"
    )


def resolve_run_model_profile(
    run_dir: str,
    *,
    profile_name: str = "",
    hf_id: str = "",
    revision: str = "",
) -> ModelProfile:
    manifest_path = os.path.join(run_dir, "run_manifest.json")
    manifest: dict[str, Any] = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)

    manifest_model = manifest.get("base_model") or {}
    manifest_hf_id = str(manifest_model.get("hf_id") or "")
    manifest_profile = str(manifest_model.get("profile") or "")
    if profile_name:
        profile = resolve_model_profile(profile_name)
        matching_manifest_profile = manifest_profile == profile_name
        if matching_manifest_profile:
            profile = _profile_from_mapping(
                manifest_profile,
                manifest_model,
                fallback=profile,
            )
        elif not manifest_profile and manifest_hf_id:
            profile = _override_model_identity(
                profile,
                hf_id=manifest_hf_id,
                revision=str(manifest_model.get("revision") or ""),
                ollama_tag=str(manifest_model.get("ollama_tag") or ""),
            )
        return _override_model_identity(
            profile,
            hf_id=hf_id,
            revision=revision,
            ollama_tag=(
                str(manifest_model.get("ollama_tag") or "")
                if matching_manifest_profile
                else ""
            ),
        )

    if manifest_profile:
        fallback = MODEL_PROFILES.get(manifest_profile)
        profile = _profile_from_mapping(
            manifest_profile,
            manifest_model,
            fallback=fallback,
        )
        return _override_model_identity(
            profile,
            hf_id=hf_id,
            revision=revision,
        )

    selected_profile = (
        infer_model_profile_name(manifest_hf_id)
        if manifest_hf_id
        else DEFAULT_MODEL_PROFILE
    )
    return resolve_model_profile(
        selected_profile,
        hf_id=hf_id or manifest_hf_id,
        revision=revision,
    )


def load_tokenizer(profile: ModelProfile):
    try:
        from transformers import AutoTokenizer  # type: ignore
    except Exception as exc:
        raise RuntimeError("Loading a tokenizer requires transformers") from exc
    return AutoTokenizer.from_pretrained(
        profile.hf_id,
        revision=profile.revision,
        use_fast=True,
    )


def load_causal_lm(profile: ModelProfile):
    import torch  # type: ignore

    dtype_by_name = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    try:
        model_dtype = dtype_by_name[profile.torch_dtype]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported torch dtype {profile.torch_dtype!r} in model profile {profile.name!r}"
        ) from exc
    load_kwargs = {"revision": profile.revision, "dtype": model_dtype}
    if profile.attention_implementation:
        load_kwargs["attn_implementation"] = profile.attention_implementation
    try:
        if profile.loader == "qwen3.5-text-causal-lm":
            from transformers import Qwen3_5ForCausalLM  # type: ignore

            return Qwen3_5ForCausalLM.from_pretrained(profile.hf_id, **load_kwargs)

        from transformers import AutoModelForCausalLM  # type: ignore

        return AutoModelForCausalLM.from_pretrained(profile.hf_id, **load_kwargs)
    except ImportError as exc:
        raise RuntimeError(
            f"The installed transformers version does not support loader {profile.loader!r}"
        ) from exc


def apply_chat_template_ids(
    tokenizer,
    messages: list[dict[str, Any]],
    *,
    add_generation_prompt: bool,
    enable_thinking: bool = False,
) -> list[int]:
    """Render chat input as a flat token-id list."""
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
        enable_thinking=enable_thinking,
        return_dict=False,
    )
    # Transformers 5 defaults apply_chat_template() to a BatchEncoding. Some
    # model-specific tokenizers may retain that shape even when asked for a
    # plain result, so accept both APIs at this compatibility boundary.
    if isinstance(encoded, dict):
        encoded = encoded["input_ids"]
    return list(encoded)


def resolve_lora_target_modules(model, profile: ModelProfile) -> list[str]:
    available_names = {
        module_name.rsplit(".", 1)[-1]
        for module_name, _module in model.named_modules()
        if module_name
    }
    selected = [
        target for target in profile.lora_target_modules if target in available_names
    ]
    missing = sorted(set(profile.lora_target_modules) - set(selected))
    if missing:
        raise ValueError(
            f"Model {profile.hf_id!r} is missing configured LoRA targets: {', '.join(missing)}"
        )
    return selected
