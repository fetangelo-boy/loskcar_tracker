from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User

from .access import (
    can_add_service,
    can_edit_order,
    can_manage_clients,
    can_remove_service,
    can_resolve_problems,
    can_toggle_service,
    can_view_internal_notes,
    can_view_prices,
    forward_targets_for,
    main_action_for,
    outcome_targets_for,
    override_targets_for,
    role_required,
)
from .audit import log_audit
from .calendar import (
    HOUR_PX,
    WEEKDAY_NAMES,
    day_schedule,
    week_schedule,
    week_start,
    work_hours,
)
from .forms import (
    AddServiceForm,
    ClientEditForm,
    CommentForm,
    OrderCreateForm,
    OrderEditForm,
    OrderEditForm as EditForm,
    ServiceForm,
    VehicleForm,
)
from .models import Client, Comment, Order, OrderEvent, Service, Vehicle
from .services import (
    StaleOrderError,
    WorkflowError,
    add_comment,
    add_services,
    add_vehicle,
    create_client,
    create_order,
    current_actor,
    find_client_matches,
    remove_service_row,
    resolve_problems,
    search_orders,
    set_order_status,
    toggle_service_done,
    update_client,
    update_order,
)
from .utils import digits_only
from .workflow import (
    BOARD_STATUSES,
    CAPACITY_STATUSES,
    OUTCOME_STATUSES,
    Status,
    ready_blocker,
    status_label,
    status_tone,
)


def error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and getattr(exc, "messages", None):
        return "; ".join(exc.messages)
    return str(exc)


def _next_url(request, fallback: str) -> str:
    value = (request.POST.get("next") or "").strip()
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return fallback


def _board_context(request) -> dict:
    actor = current_actor(request)
    query = (request.GET.get("q") or "").strip()
    show_closed = request.GET.get("closed") == "1"

    orders = Order.objects.select_related("client", "vehicle", "master").prefetch_related(
        "services"
    )
    if query:
        orders = search_orders(query)

    cutoff = timezone.now() - timedelta(days=settings.ISSUED_VISIBLE_DAYS)
    orders = orders.exclude(status__in=OUTCOME_STATUSES)
    if not show_closed:
        orders = orders.filter(
            ~Q(status=Status.ISSUED)
            | Q(status=Status.ISSUED, planned_start__gte=cutoff)
            | Q(
                status=Status.ISSUED,
                planned_start__isnull=True,
                updated_at__gte=cutoff,
            )
        )

    aggregate = orders.aggregate(last=Max("updated_at"), count=Count("id"))
    last = aggregate["last"]
    signature = f"{aggregate['count']}:{int(last.timestamp()) if last else 0}:{show_closed}"

    grouped: dict[str, list[Order]] = {status: [] for status in BOARD_STATUSES}
    for order in orders.order_by("-updated_at"):
        if order.status in grouped:
            order.forward_targets = forward_targets_for(actor, order)  # type: ignore[attr-defined]
            order.main_action = main_action_for(actor, order)  # type: ignore[attr-defined]
            order.ready_blocker = ready_blocker(order)  # type: ignore[attr-defined]
            grouped[order.status].append(order)

    columns = [
        {
            "status": status,
            "label": status_label(status),
            "tone": status_tone(status),
            "orders": grouped[status],
        }
        for status in BOARD_STATUSES
    ]

    return {
        "columns": columns,
        "query": query,
        "show_closed": show_closed,
        "signature": signature,
        "actor": actor,
        "capacity": settings.WORKSHOP_CAPACITY,
        "total_count": sum(len(column["orders"]) for column in columns),
    }


@login_required
@require_GET
def board(request):
    context = _board_context(request)
    return render(request, "tracker/board.html", context)


@login_required
@require_GET
def board_fragment(request):
    context = _board_context(request)
    response = render(request, "tracker/_board.html", context)
    response["Cache-Control"] = "no-store"
    return response


def _parse_day(raw: str | None, fallback: date) -> date:
    if raw:
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            pass
    return fallback


def _masters_for_filter() -> list[User]:
    return list(
        User.objects.filter(role=User.Role.MASTER, is_active=True).order_by(
            "first_name", "username"
        )
    )


@login_required
@require_GET
def calendar_view(request):
    today = timezone.localdate()
    mode = "day" if request.GET.get("view") == "day" else "week"
    anchor = _parse_day(request.GET.get("date"), today)

    masters = _masters_for_filter()
    master = None
    raw_master = (request.GET.get("master") or "").strip()
    if raw_master.isdigit():
        master = next((item for item in masters if item.pk == int(raw_master)), None)

    capacity = max(settings.WORKSHOP_CAPACITY, 1)
    open_hour, close_hour, hours_confirmed = work_hours()

    if mode == "day":
        schedules = [
            day_schedule(
                anchor,
                capacity=capacity,
                master=master,
                open_hour=open_hour,
                close_hour=close_hour,
                today=today,
            )
        ]
        period_start = period_end = anchor
        prev_date, next_date = anchor - timedelta(days=1), anchor + timedelta(days=1)
    else:
        schedules = week_schedule(
            anchor,
            capacity=capacity,
            master=master,
            open_hour=open_hour,
            close_hour=close_hour,
            today=today,
        )
        period_start, period_end = schedules[0].day, schedules[-1].day
        prev_date = period_start - timedelta(days=7)
        next_date = period_start + timedelta(days=7)

    return render(
        request,
        "tracker/calendar.html",
        {
            "mode": mode,
            "anchor": anchor,
            "week_start_day": week_start(anchor),
            "period_start": period_start,
            "period_end": period_end,
            "schedules": schedules,
            "capacity": capacity,
            "hours_confirmed": hours_confirmed,
            "open_hour": open_hour,
            "close_hour": close_hour,
            "hour_px": HOUR_PX,
            "total_count": sum(item.count for item in schedules),
            "conflict_total": sum(item.conflict_count for item in schedules),
            "masters": masters,
            "selected_master": master,
            "prev_date": prev_date,
            "next_date": next_date,
            "today": today,
            "weekdays": WEEKDAY_NAMES,
        },
    )


@login_required
@require_GET
def master_screen(request):
    actor = current_actor(request)
    today = timezone.localdate()
    capacity = max(settings.WORKSHOP_CAPACITY, 1)

    orders = list(
        Order.objects.filter(status__in=CAPACITY_STATUSES)
        .select_related("client", "vehicle", "master")
        .prefetch_related("services")
    )
    orders.sort(
        key=lambda order: (
            order.planned_start is None,
            order.planned_start or timezone.now(),
            order.pk,
        )
    )

    for order in orders:
        order.forward_targets = forward_targets_for(actor, order)  # type: ignore[attr-defined]
        order.main_action = main_action_for(actor, order)  # type: ignore[attr-defined]
        order.ready_blocker = ready_blocker(order)  # type: ignore[attr-defined]
        order.active_rows = order.active_service_rows  # type: ignore[attr-defined]
        order.is_today = bool(  # type: ignore[attr-defined]
            order.planned_start
            and timezone.localtime(order.planned_start).date() == today
        )
        order.can_toggle_services = order.status in {  # type: ignore[attr-defined]
            Status.ACCEPTED,
            Status.IN_PROGRESS,
        }

    today_orders = [order for order in orders if order.is_today]
    other_orders = [order for order in orders if not order.is_today]
    today_schedule = day_schedule(today, capacity=capacity)

    return render(
        request,
        "tracker/master.html",
        {
            "today": today,
            "today_orders": today_orders,
            "other_orders": other_orders,
            "today_schedule": today_schedule,
            "capacity": capacity,
            "actor": actor,
            "signature": _production_signature(),
        },
    )


def _production_signature() -> str:
    aggregate = Order.objects.filter(status__in=CAPACITY_STATUSES).aggregate(
        count=Count("id"),
        last=Max("updated_at"),
        version=Max("version"),
    )
    last = aggregate["last"]
    return (
        f"{aggregate['count']}:{aggregate['version'] or 0}:"
        f"{int(last.timestamp()) if last else 0}"
    )


@login_required
@require_GET
def master_pulse(request):
    response = JsonResponse({"signature": _production_signature()})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def order_list(request):
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    orders = search_orders(query).prefetch_related("services")
    if status in Order.Status.values:
        orders = orders.filter(status=status)
    else:
        status = ""
    orders = orders.order_by("-created_at", "-id")

    paginator = Paginator(orders, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "tracker/order_list.html",
        {
            "page_obj": page,
            "orders": page.object_list,
            "query": query,
            "status": status,
            "statuses": Order.Status.choices,
            "total": paginator.count,
        },
    )


@login_required
@require_GET
def order_detail(request, pk: int):
    order = get_object_or_404(
        Order.objects.select_related("client", "vehicle", "master", "administrator"),
        pk=pk,
    )
    services = list(
        order.services.select_related("service", "done_by").order_by("position", "id")
    )
    active_services = [row for row in services if row.is_active]
    comments = list(
        order.comments.select_related("author", "resolved_by").order_by("-created_at")
    )
    events = list(order.events.select_related("author").order_by("-created_at", "-id")[:80])
    unresolved_problems = [
        item
        for item in comments
        if item.kind == Comment.Kind.PROBLEM and item.resolved_at is None
    ]

    actor = current_actor(request)
    return render(
        request,
        "tracker/order_detail.html",
        {
            "order": order,
            "services": services,
            "active_services": active_services,
            "comments": comments,
            "events": events,
            "unresolved_problems": unresolved_problems,
            "forward_targets": forward_targets_for(actor, order),
            "outcome_targets": outcome_targets_for(actor, order),
            "override_targets": override_targets_for(actor, order),
            "main_action": main_action_for(actor, order),
            "ready_blocker": ready_blocker(order),
            "actor": actor,
            "comment_form": CommentForm(),
            "add_service_form": AddServiceForm(),
            "known_total": order.services_total,
            "known_count": order.services_known_count,
            "unknown_count": order.services_unknown_count,
            "can_view_prices": can_view_prices(actor),
            "can_edit_order": can_edit_order(actor),
            "can_add_service": can_add_service(actor),
            "can_remove_service": can_remove_service(actor),
            "can_toggle_service": can_toggle_service(actor),
            "can_resolve_problems": can_resolve_problems(actor),
            "can_edit_services": order.status
            not in {Status.ISSUED, Status.DECLINED, Status.CANCELLED, Status.NO_SHOW, Status.LOST},
        },
    )


def _order_form_context(
    form: OrderCreateForm,
    *,
    client: Client | None = None,
    matches: list[Client] | None = None,
    needs_client_choice: bool = False,
) -> dict:
    return {
        "form": form,
        "client": client,
        "matches": matches or [],
        "needs_client_choice": needs_client_choice,
    }


@role_required(User.Role.OWNER, User.Role.ADMIN)
def order_create(request):
    client = None
    if request.method == "GET":
        client_id = request.GET.get("client")
        if client_id:
            client = get_object_or_404(Client, pk=client_id)
        form = OrderCreateForm(client=client)
    else:
        client_id = request.POST.get("client_id")
        if client_id:
            client = Client.objects.filter(pk=client_id).first()
            if client is None:
                messages.error(request, "Клиент не найден. Создайте заявку заново.")
                return redirect("tracker:order_create")
        form = OrderCreateForm(request.POST, client=client)

        if form.is_valid():
            cleaned = form.cleaned_data
            matches: list[Client] = []
            chosen_client = client
            if chosen_client is None:
                matches = list(
                    find_client_matches(
                        cleaned.get("client_name", ""),
                        cleaned.get("client_phone", ""),
                    )
                )
                choice = (cleaned.get("client_choice") or "").strip()
                if matches and not choice:
                    return render(
                        request,
                        "tracker/order_form.html",
                        _order_form_context(
                            form,
                            client=client,
                            matches=matches,
                            needs_client_choice=True,
                        ),
                    )
                if choice and choice != "new":
                    try:
                        chosen_client = Client.objects.get(pk=int(choice))
                    except (ValueError, Client.DoesNotExist):
                        form.add_error(
                            None, "Выбранный клиент не найден. Выберите снова."
                        )
                        return render(
                            request,
                            "tracker/order_form.html",
                            _order_form_context(
                                form,
                                client=client,
                                matches=matches,
                                needs_client_choice=True,
                            ),
                        )
                else:
                    try:
                        chosen_client = create_client(
                            name=cleaned["client_name"],
                            phone=cleaned["client_phone"],
                            telegram=cleaned.get("client_telegram", ""),
                            actor=current_actor(request),
                        )
                    except WorkflowError as exc:
                        form.add_error(None, error_text(exc))

            if chosen_client is not None and not form.errors:
                vehicle = cleaned.get("vehicle")
                make = (cleaned.get("vehicle_make") or "").strip()
                model = (cleaned.get("vehicle_model") or "").strip()
                try:
                    if vehicle is None and make and model:
                        vehicle, _ = add_vehicle(
                            chosen_client,
                            make=make,
                            model=model,
                            plate=cleaned.get("vehicle_plate", ""),
                            actor=current_actor(request),
                        )
                    elif vehicle is None and (make or model):
                        raise WorkflowError(
                            "Для нового автомобиля укажите и марку, и модель."
                        )
                    order = create_order(
                        client=chosen_client,
                        vehicle=vehicle,
                        services=list(cleaned["services"]),
                        source=cleaned["source"],
                        comment=cleaned.get("comment", ""),
                        desired_at=cleaned.get("desired_at"),
                        planned_start=cleaned.get("planned_start"),
                        planned_end=cleaned.get("planned_end"),
                        master=cleaned.get("master"),
                        administrator=current_actor(request),
                        actor=current_actor(request),
                    )
                except WorkflowError as exc:
                    form.add_error(None, error_text(exc))
                else:
                    messages.success(
                        request, f"Заявка {order.display_number} создана."
                    )
                    return redirect("tracker:order_detail", pk=order.pk)

    return render(
        request,
        "tracker/order_form.html",
        _order_form_context(form, client=client),
    )


@role_required(User.Role.OWNER, User.Role.ADMIN)
def order_edit(request, pk: int):
    order = get_object_or_404(Order.objects.select_related("client"), pk=pk)
    if request.method == "POST":
        form = OrderEditForm(request.POST, order=order)
        if form.is_valid():
            cleaned = form.cleaned_data
            try:
                update_order(
                    order,
                    actor=current_actor(request),
                    expected_version=cleaned["version"],
                    vehicle=cleaned.get("vehicle"),
                    source=cleaned["source"],
                    comment=cleaned.get("comment", ""),
                    desired_at=cleaned.get("desired_at"),
                    planned_start=cleaned.get("planned_start"),
                    planned_end=cleaned.get("planned_end"),
                    master=cleaned.get("master"),
                )
            except WorkflowError as exc:
                form.add_error(None, error_text(exc))
            else:
                messages.success(request, "Заказ обновлён.")
                return redirect("tracker:order_detail", pk=order.pk)
    else:
        form = OrderEditForm(order=order)
    return render(request, "tracker/order_edit.html", {"form": form, "order": order})


@login_required
@require_POST
def order_status(request, pk: int):
    order = get_object_or_404(Order, pk=pk)
    target = (request.POST.get("to") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    override = request.POST.get("override") == "1"
    version_raw = (request.POST.get("version") or "").strip()
    version = int(version_raw) if version_raw.isdigit() else None
    wants_json = request.headers.get("x-requested-with") == "fetch"

    try:
        updated = set_order_status(
            order,
            target,
            actor=current_actor(request),
            reason=reason,
            expected_version=version,
            allow_override=override,
        )
    except StaleOrderError as exc:
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": error_text(exc), "stale": True}, status=409
            )
        messages.error(request, error_text(exc))
        return redirect("tracker:order_detail", pk=order.pk)
    except WorkflowError as exc:
        if wants_json:
            return JsonResponse({"ok": False, "error": error_text(exc)}, status=400)
        messages.error(request, error_text(exc))
        return redirect(_next_url(request, reverse("tracker:order_detail", args=[order.pk])))
    except Exception as exc:  # pragma: no cover - защита от неожиданных ошибок
        if wants_json:
            return JsonResponse(
                {"ok": False, "error": "Не удалось изменить статус."}, status=400
            )
        raise exc

    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "id": updated.pk,
                "status": updated.status,
                "status_label": status_label(updated.status),
                "version": updated.version,
            }
        )
    messages.success(
        request,
        f"Статус изменён: {status_label(updated.status)}.",
    )
    return redirect(_next_url(request, reverse("tracker:order_detail", args=[order.pk])))


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_POST
def order_add_service(request, pk: int):
    order = get_object_or_404(Order, pk=pk)
    form = AddServiceForm(request.POST)
    if form.is_valid():
        try:
            add_services(
                order,
                [form.cleaned_data["service"]],
                actor=current_actor(request),
            )
        except WorkflowError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, "Услуга добавлена в заказ.")
    else:
        messages.error(request, "Выберите услугу.")
    return redirect("tracker:order_detail", pk=order.pk)


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_POST
def order_remove_service(request, pk: int, row_id: int):
    order = get_object_or_404(Order, pk=pk)
    row = get_object_or_404(order.services, pk=row_id)
    reason = (request.POST.get("reason") or "").strip()
    try:
        remove_service_row(row, actor=current_actor(request), reason=reason)
    except WorkflowError as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, "Услуга убрана из активных.")
    return redirect("tracker:order_detail", pk=order.pk)


@login_required
@require_POST
def order_toggle_service(request, pk: int, row_id: int):
    order = get_object_or_404(Order, pk=pk)
    row = get_object_or_404(order.services, pk=row_id)
    version_raw = (request.POST.get("version") or "").strip()
    if version_raw.isdigit() and int(version_raw) != order.version:
        messages.error(
            request,
            "Заказ уже изменили в другом окне. Обновите страницу и повторите действие.",
        )
        return redirect(
            _next_url(request, reverse("tracker:order_detail", args=[order.pk]))
        )
    done = request.POST.get("done") == "1"
    try:
        toggle_service_done(row, done=done, actor=current_actor(request))
    except WorkflowError as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(
            request,
            "Услуга отмечена выполненной." if done else "Отметка выполнения снята.",
        )
    return redirect(_next_url(request, reverse("tracker:order_detail", args=[order.pk])))


@login_required
@require_POST
def order_comment(request, pk: int):
    order = get_object_or_404(Order, pk=pk)
    form = CommentForm(request.POST)
    if form.is_valid():
        try:
            add_comment(
                order,
                text=form.cleaned_data["text"],
                kind=form.cleaned_data["kind"],
                actor=current_actor(request),
                expected_at=form.cleaned_data.get("expected_at"),
            )
        except WorkflowError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, "Комментарий добавлен.")
    else:
        messages.error(request, "Проверьте текст комментария.")
    return redirect("tracker:order_detail", pk=order.pk)


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_POST
def order_resolve_problems(request, pk: int):
    order = get_object_or_404(Order, pk=pk)
    count = resolve_problems(order, actor=current_actor(request))
    if count:
        messages.success(request, f"Отмечено решённых проблем: {count}.")
    else:
        messages.info(request, "Открытых проблем нет.")
    return redirect("tracker:order_detail", pk=order.pk)


@login_required
@require_GET
def order_pulse(request, pk: int):
    order = get_object_or_404(Order, pk=pk)
    return JsonResponse(
        {
            "version": order.version,
            "status": order.status,
            "status_label": status_label(order.status),
            "updated_at": order.updated_at.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@login_required
@require_GET
def client_list(request):
    query = (request.GET.get("q") or "").strip()
    clients = Client.objects.annotate(
        orders_total=Count("orders", distinct=True),
        vehicles_total=Count("vehicles", distinct=True),
        last_order_at=Max("orders__created_at"),
    )
    if query:
        digits = digits_only(query)
        lookup = (
            Q(name__icontains=query)
            | Q(telegram__icontains=query)
            | Q(vehicles__plate__icontains=query)
            | Q(vehicles__make__icontains=query)
            | Q(vehicles__model__icontains=query)
        )
        if len(digits) >= 4:
            lookup |= Q(phone__contains=digits)
        clients = clients.filter(lookup).distinct()
    clients = clients.order_by("-last_order_at", "name")
    paginator = Paginator(clients, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "tracker/client_list.html",
        {
            "clients": page.object_list,
            "page_obj": page,
            "query": query,
            "total": paginator.count,
        },
    )


@login_required
@require_GET
def client_detail(request, pk: int):
    client = get_object_or_404(Client, pk=pk)
    orders = (
        client.orders.select_related("vehicle", "master")
        .prefetch_related("services")
        .order_by("-created_at")
    )
    return render(
        request,
        "tracker/client_detail.html",
        {
            "client": client,
            "orders": orders,
            "vehicles": client.vehicles.all(),
            "edit_form": ClientEditForm(client=client),
            "vehicle_form": VehicleForm(),
            "can_manage_clients": can_manage_clients(request.user),
            "can_view_internal_notes": can_view_internal_notes(request.user),
        },
    )


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_POST
def client_edit(request, pk: int):
    client = get_object_or_404(Client, pk=pk)
    form = ClientEditForm(request.POST, client=client)
    if form.is_valid():
        try:
            update_client(
                client,
                actor=current_actor(request),
                name=form.cleaned_data["name"],
                phone=form.cleaned_data["phone"],
                telegram=form.cleaned_data.get("telegram", ""),
                notes=form.cleaned_data.get("notes", ""),
                is_regular=form.cleaned_data.get("is_regular", False),
            )
        except WorkflowError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, "Карточка клиента обновлена.")
    else:
        messages.error(request, "Проверьте данные клиента.")
    return redirect("tracker:client_detail", pk=client.pk)


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_POST
def client_vehicle_add(request, pk: int):
    client = get_object_or_404(Client, pk=pk)
    form = VehicleForm(request.POST)
    if form.is_valid():
        try:
            vehicle, created = add_vehicle(
                client,
                make=form.cleaned_data["make"],
                model=form.cleaned_data["model"],
                plate=form.cleaned_data.get("plate", ""),
                actor=current_actor(request),
            )
        except WorkflowError as exc:
            messages.error(request, error_text(exc))
        else:
            if created:
                messages.success(request, f"Автомобиль {vehicle} добавлен.")
            else:
                messages.info(request, f"Автомобиль {vehicle} уже был в карточке.")
    else:
        messages.error(request, "Укажите марку и модель автомобиля.")
    return redirect("tracker:client_detail", pk=client.pk)


@role_required(User.Role.OWNER, User.Role.ADMIN)
@require_GET
def service_list(request):
    query = (request.GET.get("q") or "").strip()
    services = Service.objects.all()
    if query:
        services = services.filter(
            Q(name__icontains=query) | Q(category__icontains=query)
        )
    return render(
        request,
        "tracker/service_list.html",
        {
            "services": services,
            "query": query,
            "unconfirmed_count": Service.objects.filter(is_confirmed=False).count(),
        },
    )


@role_required(User.Role.OWNER)
def service_create(request):
    if request.method == "POST":
        form = ServiceForm(request.POST)
        if form.is_valid():
            service = form.save()
            log_audit(
                actor=current_actor(request),
                action="service_created",
                entity_type="service",
                entity_id=service.pk,
                summary=f"Создана услуга «{service.name}»",
            )
            messages.success(request, "Услуга создана.")
            return redirect("tracker:service_list")
    else:
        form = ServiceForm()
    return render(
        request,
        "tracker/service_form.html",
        {"form": form, "service": None, "title": "Новая услуга"},
    )


@role_required(User.Role.OWNER)
def service_edit(request, pk: int):
    service = get_object_or_404(Service, pk=pk)
    if request.method == "POST":
        form = ServiceForm(request.POST, instance=service)
        if form.is_valid():
            service = form.save()
            log_audit(
                actor=current_actor(request),
                action="service_updated",
                entity_type="service",
                entity_id=service.pk,
                summary=f"Изменена услуга «{service.name}»",
            )
            messages.success(request, "Услуга обновлена.")
            return redirect("tracker:service_list")
    else:
        form = ServiceForm(instance=service)
    return render(
        request,
        "tracker/service_form.html",
        {"form": form, "service": service, "title": service.name},
    )


@role_required(User.Role.OWNER)
@require_POST
def service_toggle(request, pk: int):
    service = get_object_or_404(Service, pk=pk)
    service.is_active = not service.is_active
    service.save(update_fields=["is_active", "updated_at"])
    log_audit(
        actor=current_actor(request),
        action="service_toggled",
        entity_type="service",
        entity_id=service.pk,
        summary=(
            f"Услуга «{service.name}» "
            + ("включена" if service.is_active else "скрыта")
        ),
    )
    messages.success(
        request,
        "Услуга включена." if service.is_active else "Услуга скрыта из выбора.",
    )
    return redirect("tracker:service_list")
