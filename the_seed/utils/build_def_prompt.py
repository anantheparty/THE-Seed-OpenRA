import inspect
import textwrap
from typing import Any, get_type_hints


def _safe_get_type_hints(func):
    try:
        return get_type_hints(func, include_extras=True)
    except Exception:
        return {}


def _ann_to_str(ann):
    if ann is inspect._empty:
        return None
    try:
        text = getattr(ann, "__name__", None) or str(ann)
    except Exception:
        text = str(ann)
    return text.replace("typing.", "")


def _unwrap_descriptor(owner, name):
    raw = inspect.getattr_static(owner, name)
    if isinstance(raw, staticmethod):
        return raw.__func__, "staticmethod"
    if isinstance(raw, classmethod):
        return raw.__func__, "classmethod"
    if inspect.isfunction(raw):
        return raw, "function"
    if callable(raw):
        return raw, "callable"
    raise TypeError(f"{name} is not callable")


def build_def_style_prompt(
    cls_or_obj: Any,
    fn_names: list[str],
    *,
    title: str | None = None,
    omit_first_param_for_methods: bool = True,
    include_doc_first_line: bool = True,
    include_doc_block: bool = False,
) -> str:
    owner = cls_or_obj if isinstance(cls_or_obj, type) else cls_or_obj.__class__
    header = title or f"Available functions on {owner.__name__}:"
    lines = [header]

    for name in fn_names:
        func, kind = _unwrap_descriptor(owner, name)
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            lines.append(f"- {name}: <signature unavailable>")
            continue

        hints = _safe_get_type_hints(func)
        params_out = []
        inserted_kw_star = False
        params = list(sig.parameters.values())
        if omit_first_param_for_methods and kind in {"function", "classmethod"} and params:
            params = params[1:]

        for param in params:
            chunk = ""
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                chunk += f"*{param.name}"
            elif param.kind == inspect.Parameter.VAR_KEYWORD:
                chunk += f"**{param.name}"
            else:
                if param.kind == inspect.Parameter.KEYWORD_ONLY and not inserted_kw_star:
                    has_var_positional = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in params)
                    if not has_var_positional:
                        params_out.append("*")
                    inserted_kw_star = True
                chunk += param.name

            ann = hints.get(param.name, param.annotation)
            ann_text = _ann_to_str(ann)
            if ann_text:
                chunk += f": {ann_text}"

            if param.default is not inspect._empty and param.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                try:
                    default_text = repr(param.default)
                except Exception:
                    default_text = "<default>"
                chunk += f" = {default_text}"

            params_out.append(chunk)

        ret_ann = hints.get("return", sig.return_annotation)
        ret_text = _ann_to_str(ret_ann)

        prefix = "@staticmethod\n" if kind == "staticmethod" else "@classmethod\n" if kind == "classmethod" else ""
        def_line = f"{prefix}def {name}({', '.join(params_out)})"
        if ret_text:
            def_line += f" -> {ret_text}"
        def_line += ":"

        doc = inspect.getdoc(func) or ""
        if include_doc_first_line and doc.strip():
            first = doc.strip().splitlines()[0].strip()
            lines.append(f"- {def_line}  # {first}")
        else:
            lines.append(f"- {def_line}")

        if include_doc_block and doc.strip():
            lines.append(textwrap.indent(doc, "    "))

    return "\n".join(lines)
