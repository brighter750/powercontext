依据 SPEC-PRICING-102 与 SPEC-PRICING-103 实现阶梯满减：门槛按会员折扣后的小计判断，只取最高一档，不叠加。仅修改 `src/order_pricing/discounts.py` 中的 `tiered_discount_rate`，其余文件保持不变。

```diff
--- a/src/order_pricing/discounts.py
+++ b/src/order_pricing/discounts.py
@@ -70,11 +70,13 @@
 
     依据 ``SPEC-PRICING-102``，门槛按会员折扣之后的小计判断，只取最高一档。
 
-    .. note::
-       本函数当前尚未实现阶梯规则（始终返回中性折扣率 ``1``），
-       对应的目标行为由本任务的代码修改补齐。
+    ``TIER_THRESHOLDS`` 按门槛降序排列，第一个命中的门槛即为最高一档，各档之间不叠加；
+    门槛为闭区间（``>=``），不满足任何门槛时返回中性折扣率 ``0``。
     """
 
+    for threshold, rate in TIER_THRESHOLDS:
+        if discounted_subtotal >= threshold:
+            return rate
     return NEUTRAL_DISCOUNT_RATE
 
 
```
