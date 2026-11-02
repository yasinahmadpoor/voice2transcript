"""OpenAPI metadata for clients, with defaults derived from Django forms."""

from django.http import HttpResponse, JsonResponse
from django.utils.html import format_html, format_html_join

from common.http import methods

from .defaults import transcription_defaults
from .services.prompts import (
    MAX_MEETING_DESCRIPTION,
    MAX_PARTICIPANTS,
    MAX_PARTICIPANT_DESCRIPTION,
    MAX_PARTICIPANT_NAME,
)

OPERATIONS = {
    "/health": {"get": ("Health check", "200")},
    "/auth/login": {"post": ("Sign in with username and password", "200")},
    "/auth/me": {"get": ("Read signed-in account", "200")},
    "/auth/logout": {"post": ("Revoke session", "204")},
    "/transcription/defaults": {"get": ("Read transcription defaults", "200")},
    "/transcription/providers": {
        "get": ("List enabled transcription providers", "200")
    },
    "/transcription/prompt-preview": {
        "post": (
            "Preview combined transcription instructions without a model call",
            "200",
        )
    },
    "/jobs": {
        "get": ("List recent jobs", "200"),
        "post": ("Submit a recording", "202"),
    },
    "/jobs/{job_id}": {
        "get": ("Read job status", "200"),
        "delete": ("Remove a recording", "204"),
    },
    "/jobs/{job_id}/result": {"get": ("Download transcript", "200")},
    "/jobs/{job_id}/context": {
        "get": ("Read the owner's saved meeting details and prompt snapshot", "200")
    },
    "/jobs/{job_id}/retry": {
        "post": ("Resume a failed or cancelled job, or restart explicitly", "202")
    },
    "/jobs/{job_id}/cancel": {"post": ("Cancel a queued or processing job", "202")},
    "/transcribe": {"post": ("Submit and wait for transcript", "200")},
}


@methods("GET")
def openapi(request):
    properties = {"file": {"type": "string", "format": "binary"}}
    for name, value in transcription_defaults().items():
        properties[name] = {
            "type": "array"
            if name == "participants"
            else "number"
            if name == "chunk_minutes"
            else "string",
            "default": value,
        }
    properties["chunk_minutes"].update(minimum=0, maximum=120)
    properties["language_hint"]["maxLength"] = 100
    properties["meeting_description"]["maxLength"] = MAX_MEETING_DESCRIPTION
    properties["participants"].update(
        maxItems=MAX_PARTICIPANTS, items={"$ref": "#/components/schemas/Participant"}
    )
    context_properties = {
        name: properties[name]
        for name in ("prompt", "meeting_description", "participants")
    }
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
                            "schema": {"$ref": "#/components/schemas/Recording"},
                            "encoding": {
                                "participants": {"contentType": "application/json"}
                            },
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
            if path == "/transcription/prompt-preview":
                operation["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/MeetingContext"}
                        }
                    },
                }
                operation["responses"][status]["content"] = {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"effective_prompt": {"type": "string"}},
                        }
                    }
                }
            if path == "/jobs/{job_id}/context":
                operation["responses"][status]["content"] = {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SavedContext"}
                    }
                }
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
            if path == "/jobs/{job_id}/retry":
                operation["requestBody"] = {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "restart": {"type": "boolean", "default": False}
                                },
                            }
                        }
                    },
                }
            if (
                path
                in {
                    "/jobs",
                    "/jobs/{job_id}",
                    "/jobs/{job_id}/retry",
                    "/jobs/{job_id}/cancel",
                }
                and not (path == "/jobs" and method == "get")
                and method != "delete"
            ):
                operation["responses"][status]["content"] = {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Job"}}
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
                    "Participant": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {
                                "type": "string",
                                "maxLength": MAX_PARTICIPANT_NAME,
                            },
                            "description": {
                                "type": "string",
                                "maxLength": MAX_PARTICIPANT_DESCRIPTION,
                            },
                        },
                    },
                    "MeetingContext": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": context_properties,
                    },
                    "SavedContext": {
                        "type": "object",
                        "properties": context_properties
                        | {"effective_prompt": {"type": "string"}},
                    },
                    "Job": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "queued",
                                    "processing",
                                    "cancelling",
                                    "cancelled",
                                    "failed",
                                    "completed",
                                ],
                            },
                            "retryable": {"type": "boolean"},
                            "resumable": {"type": "boolean"},
                            "restart_required": {"type": "boolean"},
                            "progress": {
                                "type": "object",
                                "properties": {
                                    "completed_chunks": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "total_chunks": {"type": "integer", "minimum": 0},
                                },
                            },
                        },
                    },
                    "Recording": {
                        "type": "object",
                        "required": ["file"],
                        "properties": properties,
                    },
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
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Transcription API</title><body><h1>Transcription API</h1><p>Sign in at <a href="/">the app</a> or POST JSON username and password to /auth/login. Keep the returned session cookie.</p><p><a href="/openapi.json">OpenAPI schema</a></p><table><thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead><tbody>{}</tbody></table><p>Upload multipart fields: file, provider, model, fallback_model, chunk_minutes, language_hint, prompt, meeting_description, participants (JSON array). Defaults are available at /transcription/defaults. Optional Idempotency-Key prevents duplicate upload retries.</p></body></html>',
            rows,
        )
    )
