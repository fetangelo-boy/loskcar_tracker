"""Расчёт календаря записи.

Модуль не знает про HTTP и шаблоны: на вход получает заказы, на выходе даёт
готовые блоки для сетки дня. Поэтому правила загрузки, дорожек и пересечений
можно проверить тестами отдельно от страниц.

Календарь не создаёт отдельную сущность «запись»: он читает те же
`planned_start` / `planned_end` заказа, что и канбан с карточкой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Order
from .workflow import CAPACITY_STATUSES

HOUR_PX = 44
MIN_BLOCK_PX = 22
WEEKDAY_NAMES = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]


@dataclass(frozen=True)
class Entry:
    """Один заказ, видимый в сетке дня."""

    order: Order
    start: datetime
    end: datetime
    actual_start: datetime
    actual_end: datetime
    lane: int
    lane_count: int
    left_pct: float
    width_pct: float
    offset_px: int
    height_px: int
    conflict: bool
    starts_before: bool
    ends_after: bool

    @property
    def time_label(self) -> str:
        start = timezone.localtime(self.start).strftime("%H:%M")
        end = timezone.localtime(self.end).strftime("%H:%M")
        if self.ends_after:
            end = "…"
        return f"{start}–{end}"


@dataclass
class DaySchedule:
    day: date
    start: datetime
    end: datetime
    weekday_name: str = ""
    entries: list[Entry] = field(default_factory=list)
    lane_count: int = 1
    peak: int = 0
    conflict_count: int = 0
    count: int = 0
    is_today: bool = False
    hours: list[str] = field(default_factory=list)
    free_windows: list[tuple[datetime, datetime]] = field(default_factory=list)

    @property
    def load_state(self) -> str:
        capacity = max(settings.WORKSHOP_CAPACITY, 1)
        if self.peak > capacity:
            return "over"
        if self.peak >= capacity:
            return "full"
        if self.peak:
            return "partial"
        return "free"


def work_hours() -> tuple[int, int, bool]:
    """Границы сетки и признак, что владелец подтвердил рабочие часы."""
    open_hour = getattr(settings, "WORKSHOP_OPEN_HOUR", None)
    close_hour = getattr(settings, "WORKSHOP_CLOSE_HOUR", None)
    if (
        open_hour is None
        or close_hour is None
        or not (0 <= open_hour < close_hour <= 24)
    ):
        return 0, 24, False
    return int(open_hour), int(close_hour), True


def day_window(day: date, open_hour: int, close_hour: int) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    midnight = datetime.combine(day, time.min, tzinfo=tz)
    return midnight + timedelta(hours=open_hour), midnight + timedelta(hours=close_hour)


def hour_labels(open_hour: int, close_hour: int) -> list[str]:
    return [f"{hour:02d}:00" for hour in range(open_hour, close_hour)]


def booked_orders(
    start: datetime,
    end: datetime,
    *,
    master=None,
) -> list[Order]:
    """Заказы, занимающие место в интервале. Записи без времени не попадают."""
    query = Order.objects.filter(
        status__in=CAPACITY_STATUSES,
        planned_start__isnull=False,
        planned_end__isnull=False,
        planned_start__lt=end,
        planned_end__gt=start,
    ).select_related("client", "vehicle", "master")
    if master is not None:
        query = query.filter(master=master)
    return list(query.order_by("planned_start", "pk"))


def _assign_lanes(items, capacity: int):
    """Раскладывает пересекающиеся интервалы по дорожкам.

    Первая свободная дорожка; если все заняты, заказ выходит за пределы
    вместимости и помечается как конфликт, но не удаляется и не скрывается.
    """
    lanes_end: list[datetime] = []
    placed: list[tuple[datetime, datetime, Order, int, bool]] = []
    for start, end, order in sorted(items, key=lambda item: (item[0], item[2].pk)):
        lane = None
        for index, lane_end in enumerate(lanes_end):
            if lane_end <= start:
                lane = index
                break
        if lane is None:
            lanes_end.append(end)
            lane = len(lanes_end) - 1
        else:
            lanes_end[lane] = max(lanes_end[lane], end)
        conflict = lane >= capacity
        placed.append((start, end, order, lane, conflict))
    return placed, max(1, len(lanes_end))


def _peak_load(entries: list[Entry]) -> int:
    points: list[tuple[datetime, int]] = []
    for entry in entries:
        points.append((entry.start, 1))
        points.append((entry.end, -1))
    points.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


def _free_windows(
    entries: list[Entry],
    start: datetime,
    end: datetime,
    capacity: int,
) -> list[tuple[datetime, datetime]]:
    if capacity <= 0:
        return []
    points: list[tuple[datetime, int]] = []
    for entry in entries:
        points.append((entry.start, 1))
        points.append((entry.end, -1))
    points.sort(key=lambda item: (item[0], item[1]))

    windows: list[tuple[datetime, datetime]] = []
    current = 0
    cursor = start
    for moment, delta in points:
        if moment > cursor and current < capacity:
            windows.append((max(cursor, start), min(moment, end)))
        current += delta
        cursor = moment
    if cursor < end and current < capacity:
        windows.append((cursor, end))

    merged: list[tuple[datetime, datetime]] = []
    for window in windows:
        if window[0] >= window[1]:
            continue
        if merged and merged[-1][1] >= window[0]:
            merged[-1] = (merged[-1][0], window[1])
        else:
            merged.append(window)
    return merged


def build_day(
    day: date,
    orders: list[Order],
    *,
    capacity: int,
    open_hour: int,
    close_hour: int,
    today: date | None = None,
) -> DaySchedule:
    start, end = day_window(day, open_hour, close_hour)
    schedule = DaySchedule(
        day=day,
        start=start,
        end=end,
        weekday_name=WEEKDAY_NAMES[day.weekday()],
        is_today=day == (today or timezone.localdate()),
        hours=hour_labels(open_hour, close_hour),
    )

    items: list[tuple[datetime, datetime, Order]] = []
    for order in orders:
        order_start, order_end = order.planned_start, order.planned_end
        if not order_start or not order_end:
            continue
        if order_end <= start or order_start >= end:
            continue
        items.append(
            (
                max(order_start, start),
                min(order_end, end),
                order,
            )
        )

    placed, lane_count = _assign_lanes(items, capacity)
    total_px = (close_hour - open_hour) * HOUR_PX
    entries: list[Entry] = []
    for clipped_start, clipped_end, order, lane, conflict in placed:
        offset = int((clipped_start - start).total_seconds() // 60 * HOUR_PX / 60)
        offset = max(0, min(offset, total_px - MIN_BLOCK_PX))
        minutes = max(1, int((clipped_end - clipped_start).total_seconds() // 60))
        height = max(MIN_BLOCK_PX, int(minutes * HOUR_PX / 60))
        height = min(height, total_px - offset)
        entries.append(
            Entry(
                order=order,
                start=clipped_start,
                end=clipped_end,
                actual_start=order.planned_start,
                actual_end=order.planned_end,
                lane=lane,
                lane_count=lane_count,
                left_pct=round(lane * 100 / lane_count, 3),
                width_pct=round(100 / lane_count, 3),
                offset_px=offset,
                height_px=height,
                conflict=conflict,
                starts_before=order.planned_start < start,
                ends_after=order.planned_end > end,
            )
        )

    entries.sort(key=lambda item: (item.start, item.order.pk))
    schedule.entries = entries
    schedule.lane_count = lane_count
    schedule.count = len(entries)
    schedule.peak = _peak_load(entries)
    schedule.conflict_count = sum(1 for entry in entries if entry.conflict)
    schedule.free_windows = _free_windows(entries, start, end, capacity)
    return schedule


def day_schedule(
    day: date,
    *,
    capacity: int,
    master=None,
    open_hour: int | None = None,
    close_hour: int | None = None,
    today: date | None = None,
) -> DaySchedule:
    open_hour = open_hour if open_hour is not None else work_hours()[0]
    close_hour = close_hour if close_hour is not None else work_hours()[1]
    start, end = day_window(day, open_hour, close_hour)
    orders = booked_orders(start, end, master=master)
    return build_day(
        day,
        orders,
        capacity=capacity,
        open_hour=open_hour,
        close_hour=close_hour,
        today=today,
    )


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_schedule(
    anchor: date,
    *,
    capacity: int,
    master=None,
    open_hour: int | None = None,
    close_hour: int | None = None,
    today: date | None = None,
) -> list[DaySchedule]:
    today = today or timezone.localdate()
    open_hour = open_hour if open_hour is not None else work_hours()[0]
    close_hour = close_hour if close_hour is not None else work_hours()[1]
    first = week_start(anchor)
    days = [first + timedelta(days=index) for index in range(7)]
    range_start, _ = day_window(days[0], open_hour, close_hour)
    _, range_end = day_window(days[-1], open_hour, close_hour)
    orders = booked_orders(range_start, range_end, master=master)
    return [
        build_day(
            day,
            orders,
            capacity=capacity,
            open_hour=open_hour,
            close_hour=close_hour,
            today=today,
        )
        for day in days
    ]
