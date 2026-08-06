"""
title: Custom valves
function_id: custom_valves
description: Admin settings.
author: SciLifeLab
version: 0.1
"""

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        requests_per_minute: int = Field(
            default=60, description="Max requests per minute per user for OI models"
        )

    def __init__(self):
        self.valves = self.Valves()
