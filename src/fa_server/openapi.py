from __future__ import annotations

OPENAPI_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "FA Server API",
        "version": "1.0.0",
        "description": "Rsync incremental sync and super-resolution service.",
    },
    "servers": [{"url": "/"}],
    "paths": {
        "/health": {
            "get": {
                "summary": "Health check",
                "responses": {"200": {"description": "Service is healthy"}},
            }
        },
        "/api/v1/sync/tasks/{folder_name}": {
            "post": {
                "summary": "Start an incremental rsync task",
                "parameters": [
                    {
                        "name": "folder_name",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "example": "raw_files_folder"},
                    }
                ],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/SyncTaskCreateForFolder"
                            }
                        }
                    },
                },
                "responses": {
                    "202": {
                        "description": "Sync task accepted",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TaskAccepted"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "409": {"description": "Folder already has a sync task"},
                },
                "security": [{"apiKeyAuth": []}],
            }
        },
        "/api/v1/sync/tasks/{folder_name}/updates": {
            "post": {
                "summary": "Update an existing target folder",
                "parameters": [
                    {
                        "name": "folder_name",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "example": "raw_files_folder"},
                    }
                ],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SyncTaskUpdate"}
                        }
                    },
                },
                "responses": {
                    "202": {
                        "description": "Folder update task accepted",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TaskAccepted"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "409": {"description": "Folder already has an active sync task"},
                },
                "security": [{"apiKeyAuth": []}],
            }
        },
        "/api/v1/sync/jobs/{job_id}": {
            "get": {
                "summary": "Get sync task status",
                "parameters": [
                    {"$ref": "#/components/parameters/JobId"},
                    {"$ref": "#/components/parameters/FolderName"},
                    {"$ref": "#/components/parameters/LocalRoot"},
                    {"$ref": "#/components/parameters/DatabaseFilename"},
                ],
                "responses": {
                    "200": {
                        "description": "Sync task status",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SyncJobStatus"}
                            }
                        },
                    },
                    "404": {"description": "Task not found"},
                },
                "security": [{"apiKeyAuth": []}],
            }
        },
        "/api/v1/super-resolution/tasks": {
            "post": {
                "summary": "Start a super-resolution task",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/SuperResolutionTaskCreate"
                            }
                        }
                    },
                },
                "responses": {
                    "202": {
                        "description": "Super-resolution task accepted",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TaskAccepted"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "409": {"description": "Folder already has an active task"},
                },
                "security": [{"apiKeyAuth": []}],
            }
        },
        "/api/v1/super-resolution/tasks/{job_id}": {
            "get": {
                "summary": "Get super-resolution task status",
                "parameters": [
                    {"$ref": "#/components/parameters/JobId"},
                    {"$ref": "#/components/parameters/FolderName"},
                    {"$ref": "#/components/parameters/LocalRoot"},
                    {"$ref": "#/components/parameters/DatabaseFilename"},
                ],
                "responses": {
                    "200": {
                        "description": "Super-resolution task status",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/SuperResolutionJobStatus"
                                }
                            }
                        },
                    },
                    "404": {"description": "Task not found"},
                },
                "security": [{"apiKeyAuth": []}],
            }
        },
    },
    "components": {
        "securitySchemes": {
            "apiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            }
        },
        "parameters": {
            "JobId": {
                "name": "job_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            },
            "FolderName": {
                "name": "folder_name",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "example": "folder_name"},
            },
            "LocalRoot": {
                "name": "local_root",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "example": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
                },
            },
            "DatabaseFilename": {
                "name": "database_filename",
                "in": "query",
                "required": False,
                "schema": {"type": "string", "default": "tasks.sqlite3"},
            },
        },
        "schemas": {
            "SuperResolutionTaskCreate": {
                "type": "object",
                "required": ["folder_name"],
                "properties": {
                    "folder_name": {"type": "string", "example": "folder_name"},
                    "local_root": {
                        "type": "string",
                        "default": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
                    },
                    "model_path": {
                        "type": "string",
                        "example": "D:/models/super_resolution/best_model.pth",
                        "description": "Absolute path passed to ep5_enhancement.load_model().",
                    },
                    "batch_size": {"type": "integer", "default": 3},
                    "process_partial_batch": {"type": "boolean", "default": True},
                    "idle_timeout_seconds": {"type": "integer", "default": 600},
                    "poll_interval_seconds": {"type": "integer", "default": 10},
                    "output_dirname": {
                        "type": "string",
                        "default": "Super_Resolution",
                    },
                    "database_filename": {
                        "type": "string",
                        "default": "tasks.sqlite3",
                    },
                },
            },
            "SyncTaskCreateForFolder": {
                "type": "object",
                "properties": {
                    "remote_base": {
                        "type": "string",
                        "default": "rsync://admin@172.24.22.29:8873/data",
                    },
                    "local_root": {
                        "type": "string",
                        "default": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
                    },
                    "rsync": {
                        "type": "string",
                        "default": "rsync",
                    },
                    "enable_transcode_rename": {"type": "boolean", "default": True},
                    "idle_timeout_seconds": {"type": "integer", "default": 600},
                    "poll_interval_seconds": {"type": "integer", "default": 30},
                    "raw_extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [".raw"],
                        "description": "When enable_transcode_rename is true, only .raw files are converted and renamed.",
                    },
                    "rsync_timeout_seconds": {"type": "integer", "default": 3600},
                    "database_filename": {
                        "type": "string",
                        "default": "tasks.sqlite3",
                    },
                },
            },
            "SyncTaskUpdate": {
                "type": "object",
                "properties": {
                    "remote_base": {
                        "type": "string",
                        "default": "rsync://admin@172.24.22.29:8873/data",
                    },
                    "local_root": {
                        "type": "string",
                        "default": "D:/Agent/ChatAgent/PWQ/FA_Server/data/rsync_data",
                    },
                    "rsync": {"type": "string", "default": "rsync"},
                    "enable_transcode_rename": {"type": "boolean", "default": True},
                    "idle_timeout_seconds": {"type": "integer", "default": 600},
                    "poll_interval_seconds": {"type": "integer", "default": 30},
                    "raw_extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [".raw"],
                        "description": "When enable_transcode_rename is true, only .raw files are converted and renamed.",
                    },
                    "rsync_timeout_seconds": {"type": "integer", "default": 3600},
                    "database_filename": {
                        "type": "string",
                        "default": "tasks.sqlite3",
                    },
                },
            },
            "TaskAccepted": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "folder_name": {"type": "string"},
                    "model_path": {"type": "string"},
                    "database_path": {"type": "string"},
                    "status_url": {"type": "string"},
                },
            },
            "SyncJobStatus": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "folder_name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "completed",
                            "partially_completed",
                            "failed",
                        ],
                    },
                    "required_file_count": {
                        "type": "integer",
                        "nullable": True,
                        "description": "Expected source file count parsed from raw_file_manifest.xml.",
                    },
                    "synced_file_count": {"type": "integer"},
                    "image_counts": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                    "local_dir": {"type": "string"},
                    "remote_url": {"type": "string"},
                    "job_kind": {"type": "string"},
                    "transcode_rename_enabled": {"type": "integer"},
                    "idle_timeout_seconds": {"type": "integer"},
                    "poll_interval_seconds": {"type": "integer"},
                    "created_at": {"type": "string"},
                    "started_at": {"type": "string", "nullable": True},
                    "finished_at": {"type": "string", "nullable": True},
                    "last_new_file_at": {"type": "string", "nullable": True},
                    "error_message": {"type": "string", "nullable": True},
                },
            },
            "SuperResolutionJobStatus": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "folder_name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "completed",
                            "partially_completed",
                            "failed",
                        ],
                    },
                    "batch_size": {"type": "integer"},
                    "process_partial_batch": {"type": "integer"},
                    "output_dir": {"type": "string"},
                    "model_path": {"type": "string"},
                    "processed_file_count": {"type": "integer"},
                    "image_counts": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                    "idle_timeout_seconds": {"type": "integer"},
                    "poll_interval_seconds": {"type": "integer"},
                    "created_at": {"type": "string"},
                    "started_at": {"type": "string", "nullable": True},
                    "finished_at": {"type": "string", "nullable": True},
                    "error_message": {"type": "string", "nullable": True},
                },
            },
        },
    },
}
