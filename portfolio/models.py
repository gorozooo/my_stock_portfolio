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

class BottomTab(models.Model):
    name = models.CharField(max_length=30)
    icon_class = models.CharField(max_length=50, default="fa fa-circle")  # FontAwesomeなど
    order = models.IntegerField(default=0)
    url_name = models.CharField(max_length=100, default="#") 

    def __str__(self):
        return self.name

class SubMenu(models.Model):
    tab = models.ForeignKey(BottomTab, on_delete=models.CASCADE, related_name='submenus')
    name = models.CharField(max_length=50)
    url = models.CharField(max_length=200)
    order = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.tab.name} → {self.name}"
