from typing import List, Dict, Any
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.core.v1_api_access import build_api_resource_id

class ApiDiscoveryService:
    @staticmethod
    def get_v1_api_resources(app: FastAPI) -> List[Dict[str, Any]]:
        """
        Scan the FastAPI application for V1 endpoints to be used as permission resources.
        
        Returns a list of dicts:
        [
            {
                "id": "POST:/api/v1/chat/completions",
                "name": "Chat Completions",
                "description": "Send a chat message...",
                "group": "Chat"
            },
            ...
        ]
        """
        resources = []
        
        # Sort routes by path for consistent ordering
        # Filter for APIRoute to access methods, path, summary etc.
        routes = [r for r in app.routes if isinstance(r, APIRoute)]
        routes.sort(key=lambda r: r.path)

        for route in routes:
            if route.path.startswith("/api/v1"):
                for method in route.methods:
                    if method in ["HEAD", "OPTIONS"]:
                        continue
                        
                    resource_id = build_api_resource_id(method, route.path)
                    # Use summary as primary name, fallback to function name
                    name = route.summary or route.name.replace("_", " ").title()
                    description = route.description or ""
                    # Use first tag as group
                    group = route.tags[0] if route.tags else "General"
                    
                    resources.append({
                        "id": resource_id,
                        "name": name,
                        "description": description,
                        "group": group,
                        "method": method,
                        "path": route.path
                    })
                    
        return resources
