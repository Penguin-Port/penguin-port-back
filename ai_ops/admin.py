from django.contrib import admin

from ai_ops.models import AIRecommendation, AnalyticsHourly, Promotion


admin.site.register([AIRecommendation, Promotion, AnalyticsHourly])
