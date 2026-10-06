#!/usr/bin/env bash
# Creates a small mixed-language monorepo with a planted bug on a branch, for testing and demos.
# Usage: scripts/make_demo_repo.sh /tmp/impact-demo
set -euo pipefail
DIR="${1:-/tmp/impact-demo}"
rm -rf "$DIR" && mkdir -p "$DIR" && cd "$DIR"
git init -q -b main
git config user.email demo@example.com && git config user.name "Demo Dev"

mkdir -p api/billing api/checkout api/invoices api/subscriptions api/tests \
  billing-service/src/main/java/com/ideas/billing billing-service/src/test/java/com/ideas/billing \
  web/src db/migrations

cat > impact.yaml <<'EOF'
modules:
  - path: api
    language: python
    test_command: python3 -m pytest -q {tests} --junitxml={junit}
  - path: billing-service
    language: java
    test_command: mvn -q test -Dtest={tests} -Dsurefire.failIfNoSpecifiedTests=false
    junit: target/surefire-reports
  - path: web
    language: typescript
    test_command: npx jest --ci {tests}
    env:
      JEST_JUNIT_OUTPUT_FILE: "{junit}"
  - path: db/migrations
    language: sql
ignore: [generated]
history_days: 180
EOF
printf '.impact/\nimpact-reports/\n__pycache__/\n' > .gitignore
touch api/__init__.py api/billing/__init__.py api/checkout/__init__.py api/invoices/__init__.py api/subscriptions/__init__.py
cat > api/conftest.py <<'EOF'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
EOF

cat > api/billing/pricing.py <<'EOF'
from datetime import date


def calculate_discount(price, coupon):
    """Returns the discount amount for a coupon."""
    if coupon is None:
        return 0
    if coupon["expires_at"] < date.today():
        return 0
    return round(price * coupon["percent"] / 100, 2)
EOF

cat > api/checkout/order_service.py <<'EOF'
from billing.pricing import calculate_discount


class OrderService:
    def total(self, price, coupon=None):
        return price - calculate_discount(price, coupon)
EOF

cat > api/invoices/invoice_builder.py <<'EOF'
from billing.pricing import calculate_discount


def build_invoice(price, coupon):
    try:
        discount = calculate_discount(price, coupon)
    except ValueError:
        discount = 0
    return {"price": price, "discount": discount}
EOF

cat > api/subscriptions/renewal_job.py <<'EOF'
from billing.pricing import calculate_discount


def renew(subscription):
    price = subscription["price"]
    return price - calculate_discount(price, subscription.get("coupon"))


def run_renewals(subscriptions):
    return [renew(s) for s in subscriptions]
EOF

cat > api/tests/test_checkout.py <<'EOF'
from datetime import date, timedelta
from checkout.order_service import OrderService


def test_total_without_coupon():
    assert OrderService().total(100) == 100


def test_total_with_expired_coupon():
    coupon = {"percent": 10, "expires_at": date.today() - timedelta(days=1)}
    assert OrderService().total(100, coupon) == 100
EOF

cat > api/tests/test_pricing.py <<'EOF'
from datetime import date, timedelta
from billing.pricing import calculate_discount


def test_valid_coupon():
    coupon = {"percent": 10, "expires_at": date.today() + timedelta(days=5)}
    assert calculate_discount(200, coupon) == 20
EOF

cat > billing-service/src/main/java/com/ideas/billing/InvoiceService.java <<'EOF'
package com.ideas.billing;

public class InvoiceService {
    public double applyTax(double amount) {
        return amount * 1.18;
    }

    public double finalAmount(double amount) {
        return applyTax(amount);
    }
}
EOF

cat > billing-service/src/test/java/com/ideas/billing/InvoiceServiceTest.java <<'EOF'
package com.ideas.billing;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class InvoiceServiceTest {
    @Test
    void appliesGst() {
        assertEquals(118.0, new InvoiceService().applyTax(100), 0.001);
    }
}
EOF

cat > web/src/cart.ts <<'EOF'
export function cartTotal(items: { price: number; qty: number }[]): number {
  return items.reduce((sum, i) => sum + i.price * i.qty, 0);
}
EOF

cat > web/src/cart.test.ts <<'EOF'
import { cartTotal } from "./cart";

describe("cart", () => {
  it("sums items", () => {
    expect(cartTotal([{ price: 10, qty: 2 }])).toBe(20);
  });
});
EOF

cat > db/migrations/V11__coupons.sql <<'EOF'
CREATE TABLE coupons (id BIGINT PRIMARY KEY, code TEXT, percent INT, expires_at DATE);
EOF

cat > api/billing/coupon_repo.py <<'EOF'
def find_active(conn):
    return conn.execute("SELECT * FROM coupons WHERE expires_at >= CURRENT_DATE").fetchall()
EOF

git add -A && git commit -qm "Initial billing, checkout and web cart"

# some history so risk signals have something to say
echo "# rounding" >> api/billing/pricing.py && git commit -qam "fix: rounding bug in calculate_discount"
echo "# tz" >> api/billing/pricing.py && git commit -qam "hotfix: expired coupon timezone issue"
echo "# null" >> api/billing/pricing.py && git commit -qam "fix: handle missing coupon percent"

# the branch with the planted bug
git checkout -qb feature/discount-rules
python3 - <<'EOF'
p = "api/billing/pricing.py"
s = open(p).read()
s = s.replace('''    if coupon["expires_at"] < date.today():
        return 0''', '''    if coupon["expires_at"] < date.today():
        raise ValueError("Coupon has expired")''')
open(p, "w").write(s)
EOF
cat > db/migrations/V12__rename_coupon_expiry.sql <<'EOF'
ALTER TABLE coupons RENAME COLUMN expires_at TO valid_until;
CREATE INDEX idx_coupons_valid ON coupons (valid_until);
EOF
git add -A && git commit -qm "Reject expired coupons explicitly and rename expiry column"
git checkout -q main
echo "Demo repo ready at $DIR (branch: feature/discount-rules)"
