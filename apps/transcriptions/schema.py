"""OpenAPI metadata for clients, with defaults derived from Django forms."""

from django.http import HttpResponse, JsonResponse
from django.utils.html import format_html, format_html_join

from common.http import methods

from .defaults import transcription_defaults

OPERATIONS = {
    "/health": {"get": ("Health check", "200")},
    "/auth/login": {"post": ("Sign in with username and password", "200")},
    "/auth/me": {"get": ("Read signed-in account", "200")},
    "/auth/logout": {"post": ("Revoke session", "204")},
    "/transcription/defaults": {"get": ("Read transcription defaults", "200")},
    "/transcription/providers": {"get": ("List enabled transcription providers", "200")},
    "/jobs": {
        "get": ("List recent jobs", "200"),
        "post": ("Submit a recording", "202"),
    },
    "/jobs/{job_id}": {
        "get": ("Read job status", "200"),
        "delete": ("Remove a recording", "204"),
    },
    "/jobs/{job_id}/result": {"get": ("Download transcript", "200")},
    "/jobs/{job_id}/retry": {"post": ("Retry a failed or cancelled job", "202")},
    "/jobs/{job_id}/cancel": {"post": ("Cancel a queued or processing job", "202")},
    "/transcribe": {"post": ("Submit and wait for transcript", "200")},
}


@methods("GET")
def openapi(request):
    properties = {"file": {"type": "string", "format": "binary"}}
    for name, value in transcription_defaults().items():
        properties[name] = {
            "type": "number" if name == "chunk_minutes" else "string",
            "default": value,
        }
    properties["chunk_minutes"].update(minimum=0, maximum=120)
    properties["language_hint"]["maxLength"] = 100
    paths = {}
    public = {"/health", "/auth/login", "/auth/logout"}
    for path, operations in OPERATIONS.items():
        paths[path] = {}
        for method, (summary, status) in operations.items():
            operation = {
                "summary": summary,
                "responses": {status: {"description": summary}},
            }
            if path not in public:
                operation["security"] = [{"session": []}]
            if "{job_id}" in path:
                operation["parameters"] = [
                    {
                        "in": "path",
                        "name": "job_id",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ]
            if path == "/jobs" and method == "get":
                operation["parameters"] = [
                    {
                        "in": "query",
                        "name": "limit",
                        "schema": {
                            "type": "integer",
                            "default": 50,
                            "minimum": 1,
                            "maximum": 100,
                        },
                    }
                ]
            if path in {"/jobs", "/transcribe"} and method == "post":
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {"$ref": "#/components/schemas/Recording"}
                        }
                    },
                }
                operation["parameters"] = [
                    {
                        "in": "header",
                        "name": "Idempotency-Key",
                        "schema": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "pattern": "^[A-Za-z0-9_-]+$",
                        },
                    }
                ]
            if path == "/auth/login":
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 150,
                                    },
                                    "password": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 1024,
                                    },
                                },
                            }
                        }
                    },
                }
            paths[path][method] = operation
    return JsonResponse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Gemini Transcription API", "version": "2.0.0"},
            "paths": paths,
            "components": {
                "securitySchemes": {
                    "session": {
                        "type": "apiKey",
                        "in": "cookie",
                        "name": "voice_session",
                    }
                },
                "schemas": {
                    "Recording": {
                        "type": "object",
                        "required": ["file"],
                        "properties": properties,
                    }
                },
            },
        }
    )


@methods("GET")
def docs(request):
    rows = format_html_join(
        "",
        "<tr><td>{}</td><td><code>{}</code></td><td>{}</td></tr>",
        (
            (method.upper(), path, info[0])
            for path, operations in OPERATIONS.items()
            for method, info in operations.items()
        ),
    )
    return HttpResponse(
        format_html(
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Transcription API</title><body><h1>Transcription API</h1><p>Sign in at <a href="/">the app</a> or POST JSON username and password to /auth/login. Keep the returned session cookie.</p><p><a href="/openapi.json">OpenAPI schema</a></p><table><thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead><tbody>{}</tbody></table><p>Upload multipart fields: file, model, fallback_model, chunk_minutes, language_hint, prompt. Defaults are available at /transcription/defaults. Optional Idempotency-Key prevents duplicate upload retries.</p></body></html>',
            rows,
        )
    )
