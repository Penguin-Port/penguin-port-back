from django.contrib import admin

from rewards.models import (
    Coupon,
    DailySpendBalance,
    ImmediateBenefitRedemption,
    RewardGrant,
    RewardTier,
    RewardTierBenefit,
)


admin.site.register(
    [
        DailySpendBalance,
        RewardTier,
        RewardTierBenefit,
        RewardGrant,
        Coupon,
        ImmediateBenefitRedemption,
    ]
)
