# -*- coding: utf-8 -*-
from django.db import models

class LineContact(models.Model):
    user_id = models.CharField(max_length=64, unique=True, db_index=True)  # Uxxxxxxxx…
    display_name = models.CharField(max_length=128, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.display_name or '(no name)'} [{self.user_id}]"