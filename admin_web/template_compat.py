"""
Compatibilidade para TemplateResponse entre assinaturas antigas e novas do Starlette.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates


def patch_jinja2_template_response() -> None:
    if getattr(Jinja2Templates, "_jung_template_response_patched", False):
        return

    original_template_response = Jinja2Templates.TemplateResponse

    def compat_template_response(self, *args, **kwargs):
        # Compatibilidade com a assinatura antiga:
        # TemplateResponse("template.html", {"request": request, ...}, status_code=...)
        if args and isinstance(args[0], str):
            name = args[0]
            context = dict(args[1]) if len(args) > 1 and isinstance(args[1], dict) else dict(kwargs.pop("context", {}) or {})
            request = kwargs.pop("request", None) or context.get("request")
            if request is None:
                raise ValueError("TemplateResponse requer 'request' no contexto.")

            if len(args) > 2 and "status_code" not in kwargs:
                kwargs["status_code"] = args[2]
            if len(args) > 3 and "headers" not in kwargs:
                kwargs["headers"] = args[3]
            if len(args) > 4 and "media_type" not in kwargs:
                kwargs["media_type"] = args[4]
            if len(args) > 5 and "background" not in kwargs:
                kwargs["background"] = args[5]

            return original_template_response(self, request, name, context=context, **kwargs)

        return original_template_response(self, *args, **kwargs)

    Jinja2Templates.TemplateResponse = compat_template_response
    Jinja2Templates._jung_template_response_patched = True
