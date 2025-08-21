from django.db import models

class Stock(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

class RealizedTrade(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

class Cash(models.Model):
    amount = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

