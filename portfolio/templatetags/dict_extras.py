# -*- coding: utf-8 -*-
from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    """dict から key の値を安全に取得"""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None