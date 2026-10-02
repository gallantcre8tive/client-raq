"""Create/update order + notify when customer sends payment evidence. Raw SQL for reliability."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def ensure_orders_columns(db: AsyncSession) -> None:
    for sql in (
        """CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            conversation_id INTEGER,
            customer_wa_id VARCHAR(50),
            customer_name VARCHAR(150),
            service_name VARCHAR(200),
            details TEXT,
            total DOUBLE PRECISION DEFAULT 0,
            currency VARCHAR(10) DEFAULT 'NGN',
            status VARCHAR(40) DEFAULT 'payment_submitted',
            status_note TEXT,
            fulfillment VARCHAR(30),
            schedule_note VARCHAR(200),
            payment_proof VARCHAR(500),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS schedule_note VARCHAR(200)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_proof VARCHAR(500)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment VARCHAR(30)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS details TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'NGN'",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status VARCHAR(40)",
        # Critical: if status is a PG ENUM, convert to text so payment_submitted always works
        "ALTER TABLE orders ALTER COLUMN status TYPE VARCHAR(40) USING status::text",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_wa_id VARCHAR(50)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS service_name VARCHAR(200)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS conversation_id INTEGER",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS company_id INTEGER",
    ):
        try:
            await db.execute(text(sql))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass


async def create_or_update_payment_order(
    db: AsyncSession,
    *,
    company_id: int,
    company_currency: str,
    from_wa: str,
    conversation_id: int | None,
    ctx: dict,
    att_id: int | None = None,
) -> int | None:
    """Always try to land an order row + notification. Returns order id or None."""
    from app.services.admin_notify import notify_company, notify_new_order, notify_payment_proof

    await ensure_orders_columns(db)

    total = 0.0
    for k in ("locked_total", "total", "quote_total", "subtotal"):
        try:
            if ctx.get(k) is not None and str(ctx.get(k)) != "":
                total = float(ctx.get(k))
                break
        except Exception:
            pass

    service_name = (
        ctx.get("service_name")
        or ctx.get("selected_service")
        or ctx.get("service")
        or "Print order"
    )
    parts = []
    for k in ("width", "height", "size_unit", "variant_label", "qty", "size", "fulfillment", "datetime", "address", "design_fee"):
        if ctx.get(k) is not None and ctx.get(k) != "":
            parts.append(f"{k}={ctx.get(k)}")
    details = "; ".join(parts) if parts else (ctx.get("details") or "")
    fulfillment = ctx.get("fulfillment")
    schedule = ctx.get("datetime") or ctx.get("schedule") or ""
    proof = f"/company/attachments/{att_id}" if att_id else (str(ctx.get("payment_proof") or "") or None)
    cur = company_currency or "NGN"
    status = "payment_submitted"

    order_id = None
    # try existing order_id in ctx
    try:
        if ctx.get("order_id"):
            order_id = int(ctx["order_id"])
    except Exception:
        order_id = None

    if order_id:
        try:
            await db.execute(text("""
                UPDATE orders SET
                    status = :st,
                    total = CASE WHEN :tot > 0 THEN :tot ELSE total END,
                    service_name = COALESCE(NULLIF(:svc, ''), service_name),
                    details = COALESCE(NULLIF(:det, ''), details),
                    payment_proof = COALESCE(:proof, payment_proof),
                    fulfillment = COALESCE(:ful, fulfillment),
                    schedule_note = COALESCE(NULLIF(:sch, ''), schedule_note),
                    conversation_id = COALESCE(:convid, conversation_id),
                    updated_at = NOW()
                WHERE id = :oid AND company_id = :cid
            """), {
                "st": status, "tot": total, "svc": str(service_name)[:200],
                "det": (details or "")[:2000], "proof": proof, "ful": fulfillment,
                "sch": str(schedule)[:200] if schedule else "",
                "convid": conversation_id, "oid": order_id, "cid": company_id,
            })
            await db.commit()
            print("order_updated", order_id)
        except Exception as e:
            print("order_update_fail", type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
            order_id = None

    if not order_id:
        # latest non-terminal order for this customer
        try:
            row = (await db.execute(text("""
                SELECT id FROM orders
                WHERE company_id = :cid AND customer_wa_id = :wa
                  AND COALESCE(status, '') NOT IN ('completed', 'rejected', 'cancelled')
                ORDER BY id DESC LIMIT 1
            """), {"cid": company_id, "wa": from_wa})).first()
            if row:
                order_id = int(row[0])
                await db.execute(text("""
                    UPDATE orders SET
                        status = :st,
                        total = CASE WHEN :tot > 0 THEN :tot ELSE total END,
                        service_name = COALESCE(NULLIF(:svc, ''), service_name),
                        details = COALESCE(NULLIF(:det, ''), details),
                        payment_proof = COALESCE(:proof, payment_proof),
                        fulfillment = COALESCE(:ful, fulfillment),
                        schedule_note = COALESCE(NULLIF(:sch, ''), schedule_note),
                        conversation_id = COALESCE(:convid, conversation_id),
                        updated_at = NOW()
                    WHERE id = :oid
                """), {
                    "st": status, "tot": total, "svc": str(service_name)[:200],
                    "det": (details or "")[:2000], "proof": proof, "ful": fulfillment,
                    "sch": str(schedule)[:200] if schedule else "",
                    "convid": conversation_id, "oid": order_id,
                })
                await db.commit()
                print("order_reused", order_id)
        except Exception as e:
            print("order_reuse_fail", type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
            order_id = None

    if not order_id:
        try:
            row = (await db.execute(text("""
                INSERT INTO orders (
                    company_id, conversation_id, customer_wa_id, service_name, details,
                    total, currency, status, fulfillment, schedule_note, payment_proof, created_at, updated_at
                ) VALUES (
                    :cid, :convid, :wa, :svc, :det,
                    :tot, :cur, :st, :ful, :sch, :proof, NOW(), NOW()
                ) RETURNING id
            """), {
                "cid": company_id,
                "convid": conversation_id,
                "wa": from_wa,
                "svc": str(service_name)[:200],
                "det": (details or "")[:2000],
                "tot": total,
                "cur": cur[:10],
                "st": status,
                "ful": fulfillment,
                "sch": str(schedule)[:200] if schedule else None,
                "proof": proof,
            })).first()
            await db.commit()
            order_id = int(row[0]) if row else None
            print("order_inserted", order_id, "company=", company_id, "wa=", from_wa, "total=", total)
        except Exception as e:
            print("order_insert_FAIL", type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
            # If status is a PG enum type, try without casting issues by using enum label
            try:
                row = (await db.execute(text("""
                    INSERT INTO orders (
                        company_id, conversation_id, customer_wa_id, service_name, details,
                        total, currency, fulfillment, payment_proof, created_at
                    ) VALUES (
                        :cid, :convid, :wa, :svc, :det,
                        :tot, :cur, :ful, :proof, NOW()
                    ) RETURNING id
                """), {
                    "cid": company_id, "convid": conversation_id, "wa": from_wa,
                    "svc": str(service_name)[:200], "det": (details or "")[:2000],
                    "tot": total, "cur": cur[:10], "ful": fulfillment, "proof": proof,
                })).first()
                await db.commit()
                order_id = int(row[0]) if row else None
                if order_id:
                    try:
                        await db.execute(text("UPDATE orders SET status = 'payment_submitted' WHERE id = :id"), {"id": order_id})
                        await db.commit()
                    except Exception:
                        try:
                            await db.rollback()
                        except Exception:
                            pass
                print("order_inserted_fallback", order_id)
            except Exception as e2:
                print("order_insert_fallback_FAIL", type(e2).__name__, e2)
                try:
                    await db.rollback()
                except Exception:
                    pass

    # link attachment
    if att_id and order_id:
        try:
            await db.execute(text(
                "UPDATE attachments SET order_id = :oid, kind = 'payment_proof' WHERE id = :aid AND company_id = :cid"
            ), {"oid": order_id, "aid": int(att_id), "cid": company_id})
            await db.commit()
        except Exception as e:
            print("link_att", e)
            try:
                await db.rollback()
            except Exception:
                pass

    # notification — always
    link = f"/company/orders/{order_id}" if order_id else "/company/orders"
    body = (
        "Customer: " + str(from_wa) + "\n"
        + "Service: " + str(service_name) + "\n"
        + "Details: " + str(details or "—") + "\n"
        + "Amount: " + str(cur) + " " + f"{total:,.0f}" + "\n"
        + "Fulfillment: " + str(fulfillment or "—") + "\n"
        + "Status: Payment screenshot received — verify now\n"
        + "Order: #" + str(order_id or "pending")
    )
    await notify_company(
        db,
        company_id=company_id,
        title="NEW PAYMENT SCREENSHOT",
        body=body,
        priority="high",
        conversation_id=conversation_id,
        link_path=link,
    )
    if order_id:
        await notify_company(
            db,
            company_id=company_id,
            title="NEW ORDER",
            body=f"Order #{order_id} — {service_name} — {cur} {total:,.0f} — {from_wa}",
            priority="high",
            conversation_id=conversation_id,
            link_path=link,
        )

    if order_id:
        ctx["order_id"] = order_id
    
    try:
        await notify_payment_proof(
            db,
            company_id=int(company_id),
            order_id=int(order_id) if order_id else None,
            amount=float(total) if total is not None else None,
            currency=str(cur or "NGN"),
            customer_label=str(from_wa or ""),
            conversation_id=int(conversation_id) if conversation_id else None,
            attachment_path=(f"/company/attachments/{att_id}" if att_id else None),
        )
        await notify_new_order(
            db,
            company_id=int(company_id),
            order_id=int(order_id) if order_id else None,
            service_name=str(service_name or "Order"),
            total=float(total) if total is not None else None,
            currency=str(cur or "NGN"),
            customer_label=str(from_wa or ""),
            conversation_id=int(conversation_id) if conversation_id else None,
        )
    except Exception as _ne:
        print("order_notify_extra", _ne)

    return order_id
