"""stage.csv visits joined to their check-ins (metrics.culture.stage_visits)."""
from datetime import date

from metrics.culture import stage_kind, stage_visits


def test_kind_from_category_then_words():
    assert stage_kind("Opera House", "Большой театр", "Кармен") == "opera"
    assert stage_kind("", "Белорусская государственная филармония") == "concert"
    assert stage_kind("Plaza", "Faliro Olympic Complex", "Release Athens 2023") == "concert"
    assert stage_kind("", "Театр Чехова", "Король Лир") == "theatre"
    assert stage_kind("Theater", "Badisches Staatstheater", "Sinfoniekonzert 7") == "concert"
    assert stage_kind("Opera House", "Одесский театр оперы", "Концерт Ассии Ахат") == "concert"


def test_visit_takes_venue_and_trip_from_its_checkin():
    r = {"checkin_id": "c1", "venue": "Astana Opera", "venue_id": "v1", "category": "Opera House",
         "city": "Astana", "country": "Kazakhstan", "lat": "51.1", "lng": "71.4"}
    tl = [(1655222400, date(2022, 6, 14), r)]
    spans = [("2022-06-10", "2022-06-16", 7, "Kazakhstan")]
    out = stage_visits([{"date": "2022-06-14", "place": "Астана Опера", "city": "Астана",
                         "show": "Дон Жуан", "checkin_id": "c1"},
                        {"date": "", "place": "Театр Чехова", "city": "Кишинёв", "show": "Король Лир",
                         "checkin_id": ""}], tl, spans)
    assert out[0]["venue_id"] == "v1" and out[0]["kind"] == "opera" and out[0]["trip"] == 7
    assert out[1]["date"] == "" and "venue" not in out[1] and out[1]["kind"] == "theatre"
