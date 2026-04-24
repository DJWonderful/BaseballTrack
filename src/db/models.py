"""SQLAlchemy ORM models for the milb schema."""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, SmallInteger, BigInteger, Text, Date, Boolean, Numeric,
    ForeignKey, UniqueConstraint, Index, text
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Sport(Base):
    __tablename__ = "sports"
    __table_args__ = {"schema": "milb"}

    sport_id = Column(Integer, primary_key=True)
    sport_name = Column(Text, nullable=False)
    sport_code = Column(Text)
    sort_order = Column(SmallInteger)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    teams = relationship("Team", back_populates="sport")


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = {"schema": "milb"}

    league_id = Column(Integer, primary_key=True)
    league_name = Column(Text, nullable=False)
    sport_id = Column(Integer, ForeignKey("milb.sports.sport_id"))
    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    sport = relationship("Sport")
    divisions = relationship("Division", back_populates="league")
    teams = relationship("Team", back_populates="league")


class Division(Base):
    __tablename__ = "divisions"
    __table_args__ = {"schema": "milb"}

    division_id = Column(Integer, primary_key=True)
    division_name = Column(Text, nullable=False)
    league_id = Column(Integer, ForeignKey("milb.leagues.league_id"))
    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    league = relationship("League", back_populates="divisions")
    teams = relationship("Team", back_populates="division")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "milb"}

    org_id = Column(Integer, primary_key=True)
    org_name = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    teams = relationship("Team", back_populates="organization")


class Venue(Base):
    __tablename__ = "venues"
    __table_args__ = {"schema": "milb"}

    venue_id = Column(Integer, primary_key=True)
    venue_name = Column(Text, nullable=False)
    city = Column(Text)
    state = Column(Text)
    state_abbrev = Column(Text)
    postal_code = Column(Text)
    country = Column(Text)
    latitude = Column(Numeric(10, 6))
    longitude = Column(Numeric(10, 6))
    capacity = Column(Integer)
    turf_type = Column(Text)
    roof_type = Column(Text)
    left_line = Column(Integer)
    left_center = Column(Integer)
    center_field = Column(Integer)
    right_center = Column(Integer)
    right_line = Column(Integer)
    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    teams = relationship("Team", back_populates="venue")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = {"schema": "milb"}

    team_id = Column(Integer, primary_key=True)
    team_name = Column(Text, nullable=False)
    short_name = Column(Text)
    abbreviation = Column(Text)
    location_name = Column(Text)
    team_code = Column(Text)
    sport_id = Column(Integer, ForeignKey("milb.sports.sport_id"))
    league_id = Column(Integer, ForeignKey("milb.leagues.league_id"))
    division_id = Column(Integer, ForeignKey("milb.divisions.division_id"))
    org_id = Column(Integer, ForeignKey("milb.organizations.org_id"))
    venue_id = Column(Integer, ForeignKey("milb.venues.venue_id"))
    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    sport = relationship("Sport", back_populates="teams")
    league = relationship("League", back_populates="teams")
    division = relationship("Division", back_populates="teams")
    organization = relationship("Organization", back_populates="teams")
    venue = relationship("Venue", back_populates="teams")


class Game(Base):
    __tablename__ = "games"
    __table_args__ = {"schema": "milb"}

    game_pk = Column(Integer, primary_key=True)
    game_date = Column(Date, nullable=False)
    game_datetime = Column(TIMESTAMP(timezone=True))
    season = Column(SmallInteger, nullable=False)
    game_type = Column(Text, nullable=False, server_default=text("'R'"))
    day_night = Column(Text)
    doubleheader = Column(Text)
    game_number = Column(SmallInteger)
    scheduled_innings = Column(SmallInteger, server_default=text("9"))

    status_code = Column(Text)
    status_detail = Column(Text)
    abstract_game_state = Column(Text)

    home_team_id = Column(Integer, ForeignKey("milb.teams.team_id"), nullable=False)
    home_team_name = Column(Text)
    away_team_id = Column(Integer, ForeignKey("milb.teams.team_id"), nullable=False)
    away_team_name = Column(Text)
    home_score = Column(SmallInteger)
    away_score = Column(SmallInteger)

    venue_id = Column(Integer, ForeignKey("milb.venues.venue_id"))
    venue_name = Column(Text)

    series_description = Column(Text)

    attendance = Column(Integer)
    game_duration_minutes = Column(Integer)
    first_pitch = Column(TIMESTAMP(timezone=True))

    weather_condition = Column(Text)
    weather_temp_f = Column(SmallInteger)
    weather_wind = Column(Text)

    sport_id = Column(Integer, ForeignKey("milb.sports.sport_id"))

    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    venue = relationship("Venue")
    promotions = relationship("GamePromotion", back_populates="game", cascade="all, delete-orphan")
    weather = relationship("GameWeather", back_populates="game", uselist=False, cascade="all, delete-orphan")


class GamePromotion(Base):
    __tablename__ = "game_promotions"
    __table_args__ = (
        UniqueConstraint("game_pk", "offer_id", name="uq_game_promotion"),
        {"schema": "milb"},
    )

    promotion_id = Column(BigInteger, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, ForeignKey("milb.games.game_pk", ondelete="CASCADE"), nullable=False)
    offer_id = Column(Integer)
    offer_name = Column(Text)
    offer_type = Column(Text)
    description = Column(Text)
    distribution = Column(Text)
    presented_by = Column(Text)
    image_url = Column(Text)
    thumbnail_url = Column(Text)
    display_order = Column(SmallInteger)

    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    game = relationship("Game", back_populates="promotions")


class GameWeather(Base):
    __tablename__ = "game_weather"
    __table_args__ = (
        UniqueConstraint("game_pk", name="uq_game_weather"),
        {"schema": "milb"},
    )

    weather_id = Column(BigInteger, primary_key=True, autoincrement=True)
    game_pk = Column(Integer, ForeignKey("milb.games.game_pk", ondelete="CASCADE"), nullable=False)
    venue_id = Column(Integer, ForeignKey("milb.venues.venue_id"))
    weather_date = Column(Date, nullable=False)

    temperature_max_f = Column(Numeric(5, 1))
    temperature_min_f = Column(Numeric(5, 1))
    apparent_temperature_max_f = Column(Numeric(5, 1))
    apparent_temperature_min_f = Column(Numeric(5, 1))

    precipitation_sum_in = Column(Numeric(6, 3))
    rain_sum_in = Column(Numeric(6, 3))
    snowfall_sum_in = Column(Numeric(6, 3))
    precipitation_hours = Column(Numeric(4, 1))

    windspeed_max_mph = Column(Numeric(5, 1))
    windgusts_max_mph = Column(Numeric(5, 1))
    winddirection_dominant_deg = Column(SmallInteger)

    weathercode = Column(SmallInteger)

    sunrise = Column(TIMESTAMP(timezone=True))
    sunset = Column(TIMESTAMP(timezone=True))
    sunshine_duration_sec = Column(Numeric(8, 1))

    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    game = relationship("Game", back_populates="weather")


class SeasonAttendance(Base):
    __tablename__ = "season_attendance"
    __table_args__ = (
        UniqueConstraint("team_id", "season", "game_type_id", name="uq_team_season_attendance"),
        {"schema": "milb"},
    )

    season_attendance_id = Column(BigInteger, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("milb.teams.team_id"), nullable=False)
    season = Column(SmallInteger, nullable=False)
    game_type_id = Column(Text)

    openings_total = Column(Integer)
    openings_total_home = Column(Integer)
    openings_total_away = Column(Integer)

    games_total = Column(Integer)
    games_home_total = Column(Integer)
    games_away_total = Column(Integer)

    attendance_total = Column(Integer)
    attendance_total_home = Column(Integer)
    attendance_total_away = Column(Integer)

    attendance_avg_home = Column(Integer)
    attendance_avg_away = Column(Integer)
    attendance_avg_ytd = Column(Integer)
    attendance_opening_avg = Column(Integer)

    attendance_high = Column(Integer)
    attendance_high_date = Column(Date)
    attendance_high_game_pk = Column(Integer)

    attendance_low = Column(Integer)
    attendance_low_date = Column(Date)
    attendance_low_game_pk = Column(Integer)

    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))

    team = relationship("Team")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("mlb_transaction_id", "player_id", "transaction_date", "type_code",
                         name="uq_transaction"),
        {"schema": "milb"},
    )

    transaction_id = Column(BigInteger, primary_key=True, autoincrement=True)
    mlb_transaction_id = Column(Integer)
    transaction_date = Column(Date, nullable=False)
    effective_date = Column(Date)
    resolution_date = Column(Date)

    player_id = Column(Integer, nullable=False)
    player_name = Column(Text, nullable=False)
    player_position = Column(Text)

    mlb_debut_date = Column(Date)
    is_mlb_veteran = Column(Boolean, server_default=text("FALSE"))

    from_team_id = Column(Integer)
    from_team_name = Column(Text)
    to_team_id = Column(Integer)
    to_team_name = Column(Text)

    type_code = Column(Text, nullable=False)
    type_desc = Column(Text)
    is_rehab = Column(Boolean, server_default=text("FALSE"))
    description = Column(Text)

    raw_json = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))


class DataSyncLog(Base):
    __tablename__ = "data_sync_log"
    __table_args__ = {"schema": "milb"}

    sync_id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False)
    sport_id = Column(Integer)
    season = Column(SmallInteger)
    sync_started_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    sync_ended_at = Column(TIMESTAMP(timezone=True))
    status = Column(Text, nullable=False, server_default=text("'running'"))
    records_fetched = Column(Integer, server_default=text("0"))
    records_upserted = Column(Integer, server_default=text("0"))
    error_message = Column(Text)
    parameters = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True))
