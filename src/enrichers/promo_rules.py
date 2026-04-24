"""High-confidence keyword rules for promo classification.

Philosophy: we only classify via rules when the offer_name (plus optional
description) contains an UNAMBIGUOUS signal. Any promo whose name could
plausibly belong to multiple categories gets returned as None so the
LLM handles it. This keeps rules-tier output trustworthy -- a user
reviewing a `enrichment_method='rules'` row should always agree with
the classification by reading the name.

Design:
  - Rules are either `definitive` (fully classify the promo on their own)
    or `augmentative` (add flags on top of other matches but never alone
    trigger a full classification).
  - `classify()` returns a dict shaped exactly like
    enrich_promotions.sanitize() output, OR None if no definitive rule
    fires.
  - Multiple definitive rules can fire on the same name (e.g. "Fireworks
    Friday Bobblehead Night" lights up both fireworks + giveaway). The
    flag union is kept; category goes to the highest-priority match.

To add a rule: append to PROMO_RULES with an explanatory `why` field.
Comment-explain in `why` if the match is non-obvious.

Not covered on purpose (let the LLM handle these):
  - "Sunday Funday", "Winning Wednesday", "Silver Sluggers" -- ambiguous
    without context
  - "Twisted Tuesday", "Thursday Night Lights" -- unclear which flag
  - One-off celebrity appearances named for the celebrity
  - Brand/sponsor-only names ("Pepsi Night", "Nissan Dollar Night")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical flag names match the columns on milb.game_promotions.
FLAG_DEFAULTS = {
    "is_fireworks":       False,
    "is_giveaway_item":   False,
    "is_food_deal":       False,
    "is_ticket_deal":     False,
    "is_theme_night":     False,
    "is_heritage_night":  False,
    "is_kids_event":      False,
    "is_community_event": False,
    "is_autographs":      False,
    "is_entertainment":   False,
    "is_recurring":       False,
    "is_dog_friendly":    False,
    "has_celebrity":      False,
}


@dataclass
class Rule:
    id: str
    pattern: re.Pattern
    definitive: bool
    flags: dict = field(default_factory=dict)
    category: str | None = None
    audience: str | None = None
    priority: int = 50  # higher = wins category tie-breaker
    why: str = ""


def _rx(*alts: str) -> re.Pattern:
    """Compile a case-insensitive regex. Handles word-boundary cases."""
    return re.compile("|".join(alts), re.IGNORECASE)


# Order matters for priority ties: first match wins for category.
# Within the list, group by domain so additions are easy to locate.
PROMO_RULES: list[Rule] = [
    # ----- Fireworks -----------------------------------------------------
    Rule(
        id="fireworks_any",
        pattern=_rx(r"\bfireworks?\b"),
        definitive=True,
        flags={"is_fireworks": True},
        category="fireworks",
        priority=90,
        why="Any mention of fireworks = fireworks show. Common variants: "
            "'Fireworks Friday', 'Post-game Fireworks', 'Fireworks Extravaganza'.",
    ),

    # ----- Giveaway items ------------------------------------------------
    Rule(
        id="giveaway_bobblehead",
        pattern=_rx(r"\bbobble ?head\b|\bbobble\b"),
        definitive=True,
        flags={"is_giveaway_item": True},
        category="giveaway",
        priority=85,
        why="Bobbleheads are the canonical MiLB giveaway. "
            "Always a physical item distributed to fans.",
    ),
    Rule(
        id="giveaway_apparel",
        pattern=_rx(
            r"\b(?:jersey|cap|hat|t.?shirt|tshirt|hoodie|beanie|sock|"
            r"backpack|bag|tote|blanket|poster|pennant|magnet|keychain|"
            r"rally towel|foam finger|mini[- ]bat|replica)\b.{0,20}giveaway",
            r"giveaway.{0,20}\b(?:jersey|cap|hat|t.?shirt|tshirt|hoodie|"
            r"beanie|sock|backpack|bag|tote|blanket|poster|pennant|magnet|"
            r"keychain|rally towel|foam finger|mini[- ]bat|replica)\b",
        ),
        definitive=True,
        flags={"is_giveaway_item": True},
        category="giveaway",
        priority=85,
        why="An apparel/item paired explicitly with 'giveaway' unambiguously "
            "identifies a physical giveaway.",
    ),
    Rule(
        id="giveaway_generic",
        pattern=_rx(r"\bgiveaway\b"),
        definitive=True,
        flags={"is_giveaway_item": True},
        category="giveaway",
        priority=80,
        why="Plain 'giveaway' without a stated item is still a giveaway promo.",
    ),

    # ----- Food / drink deals -------------------------------------------
    Rule(
        id="food_dollar_menu",
        pattern=_rx(
            r"\$\d+(?:\.\d{2})?\s*(?:hot ?dog|beer|wine|margarita|taco|burger|"
            r"slice|nacho|wing|soda|pretzel|pizza|lemonade|cotton candy)",
            r"\bdollar (?:dog|beer|hot ?dog|wine)\b",
            r"\b(?:ten cent|dime|nickel)\s*(?:dog|beer|hot ?dog)\b",
        ),
        definitive=True,
        flags={"is_food_deal": True},
        category="food_deal",
        priority=80,
        why="'$2 Hot Dog' or 'Dollar Beer' is always a food-deal. The price "
            "pattern is unambiguous.",
    ),
    Rule(
        id="food_thirsty_thursday",
        pattern=_rx(r"\bthirsty thurs?(?:day)?\b"),
        definitive=True,
        flags={"is_food_deal": True, "is_recurring": True},
        category="food_deal",
        audience="adults",
        priority=85,
        why="Thirsty Thursday is an MiLB-wide recurring drink special. "
            "Explicitly called out in the LLM prompt, too.",
    ),
    Rule(
        id="food_themed_weekday_drink",
        pattern=_rx(
            r"\btaco tuesday\b",
            r"\bwine wednesday\b",
            r"\bwhite claw wednesday\b",
            r"\bhappy hour\b",
            r"\bmargarita monday\b",
        ),
        definitive=True,
        flags={"is_food_deal": True, "is_recurring": True},
        category="food_deal",
        audience="adults",
        priority=80,
        why="Day-of-week drink/food themes are ongoing weekly food deals. "
            "Recurring because they happen on every matching weekday.",
    ),

    # ----- Ticket deals --------------------------------------------------
    Rule(
        id="ticket_dollar_menu",
        pattern=_rx(
            r"\$\d+(?:\.\d{2})?\s*(?:ticket|admission|seat|box seat|lawn|berm|ga\b)",
            r"\bhalf.?price (?:ticket|admission|seat)\b",
            r"\bbuy one get one\b|\bbogo\b",
            r"\b2.?for.?1\b|\btwo.?for.?one\b",
            r"\bfamily (?:4|four|pack) (?:ticket|deal|special)\b",
        ),
        definitive=True,
        flags={"is_ticket_deal": True},
        category="ticket_deal",
        priority=80,
        why="Discounted admission patterns. Family 4-pack and BOGO are "
            "unambiguous ticket promos.",
    ),
    Rule(
        id="ticket_dollar_tuesday",
        pattern=_rx(r"\$\d+\s*tuesday\b|\bdollar tuesday\b"),
        definitive=True,
        flags={"is_ticket_deal": True, "is_recurring": True},
        category="ticket_deal",
        priority=80,
        why="'$2 Tuesday' and variants -- recurring value-ticket night.",
    ),

    # ----- Theme nights (IP / brand) ------------------------------------
    Rule(
        id="theme_branded_ip",
        pattern=_rx(
            r"\bstar wars\b",
            r"\bmarvel\b|\bavengers?\b|\bspider.?man\b|\bbatman\b|\bsuperman\b",
            r"\bharry potter\b|\bwizarding\b",
            r"\bpok[eé]mon\b",
            r"\bdisney\b",
            r"\bpixar\b",
            r"\bfrozen\b",
            r"\bprincess night\b",
            r"\bsesame street\b|\bbig bird\b",
            r"\bbluey\b",
            r"\bminions?\b",
            r"\bninja turtles?\b|\btmnt\b",
            r"\btaylor swift\b|\bswiftie\b",
            r"\bmargaritaville\b|\bjimmy buffett\b",
            r"\btrolls? night\b",
            r"\bbarbie night\b",
            r"\bjurassic\b",
            r"\bgame of thrones\b",
        ),
        definitive=True,
        flags={"is_theme_night": True},
        category="theme_night",
        audience="families",
        priority=75,
        why="Named IP/franchise themes are always theme nights with costumes/"
            "decor/music around the IP.",
    ),
    Rule(
        id="theme_decade",
        pattern=_rx(
            r"\b(?:70s|80s|90s|2000s) night\b",
            r"\bthrowback\b|\btbt\b",
            r"\bturn.?back the clock\b|\bretro night\b",
        ),
        definitive=True,
        flags={"is_theme_night": True},
        category="theme_night",
        priority=70,
        why="Decade/retro nights are always themed events.",
    ),
    Rule(
        id="theme_holiday",
        pattern=_rx(
            r"\bchristmas in july\b",
            r"\bhalloween\b|\btrick.?or.?treat\b",
            r"\bst\.? patrick\b",
            r"\beaster\b",
            r"\bmother.?s day\b",
            r"\bfather.?s day\b",
            r"\bvalentine\b",
            r"\bcinco de mayo\b",
            r"\bmardi gras\b",
        ),
        definitive=True,
        flags={"is_theme_night": True},
        category="theme_night",
        priority=70,
        why="Holiday-themed games. Note: Mother's/Father's Day get theme-night, "
            "NOT heritage-night, because the theming is the main driver.",
    ),

    # ----- Heritage / cultural ------------------------------------------
    Rule(
        id="heritage_cultural",
        pattern=_rx(
            r"\b(?:hispanic|latin|latino|latina|noche latin)\w*",
            r"\b(?:irish|german|italian|polish|czech|greek|scottish)\s*(?:heritage|night|day|weekend)\b",
            r"\basian (?:american )?heritage\b|\baapi\b",
            r"\bblack heritage\b|\bjuneteenth\b|\bblack history\b|\bafrican.?american\b",
            r"\bjewish heritage\b|\bhanukkah\b",
            r"\bnative american (?:heritage|night)\b",
            r"\bheritage (?:night|day|weekend)\b",
            r"\bhispanic heritage\b",
        ),
        definitive=True,
        flags={"is_heritage_night": True},
        category="heritage_night",
        priority=80,
        why="Cultural / ethnic heritage nights. These are always heritage_night "
            "regardless of whether there's also a giveaway component.",
    ),
    Rule(
        id="heritage_pride",
        pattern=_rx(r"\bpride (?:night|day|weekend)\b|\blgbtq\b|\brainbow night\b"),
        definitive=True,
        flags={"is_heritage_night": True},
        category="heritage_night",
        priority=80,
        why="Pride Night is a community/identity celebration -- heritage_night.",
    ),
    Rule(
        id="heritage_service",
        pattern=_rx(
            r"\bmilitary appreciat\w*",
            r"\bveteran(?:s)?(?:'|'|s)?\s*(?:night|day|appreciat|salute)",
            r"\barmed forces\b",
            r"\bsalute to service\b",
            r"\bfirst responders?(?:'|')?\s*(?:night|day|appreciat)",
            r"\bpolice appreciat\w*|\bfire(?:fighters?)? appreciat\w*",
            r"\bnurse appreciat\w*|\bhealthcare appreciat\w*|\bteacher appreciat\w*",
        ),
        definitive=True,
        flags={"is_heritage_night": True, "is_community_event": True},
        category="heritage_night",
        audience="military",
        priority=80,
        why="Military/veteran/first-responder appreciation is always heritage + "
            "community. Target_audience='military' captures the service-member "
            "framing; variants like 'Teacher Appreciation' are still community "
            "events with a specific audience.",
    ),

    # ----- Kids / family / school ---------------------------------------
    Rule(
        id="kids_run_bases",
        pattern=_rx(r"\bkids? run the bases\b"),
        definitive=True,
        flags={"is_kids_event": True},
        category="kids_event",
        audience="kids",
        priority=75,
        why="'Kids Run the Bases' is a ubiquitous MiLB post-game kids activity. "
            "Must include 'kids'/'kid' -- 'Adults Run the Bases' is a separate "
            "promo that's explicitly NOT for kids.",
    ),
    Rule(
        id="kids_named",
        pattern=_rx(
            r"\bkids eat free\b",
            r"\blittle league(?:rs?)? (?:day|night)\b",
            r"\byouth baseball\b",
            r"\bkids club (?:night|day)\b",
            r"\bkid.?s day\b",
            r"\bkids? night\b",
        ),
        definitive=True,
        flags={"is_kids_event": True},
        category="kids_event",
        audience="kids",
        priority=70,
        why="Explicit kids-focused event names.",
    ),
    Rule(
        id="education_school",
        pattern=_rx(
            r"\beducation day\b",
            r"\bschool day\b",
            r"\bstudent(?:s)?(?:'|')?\s*day\b",
            r"\breading day\b|\bliteracy (?:day|night)\b",
            r"\bback to school\b",
        ),
        definitive=True,
        flags={"is_kids_event": True, "is_community_event": True},
        category="community_event",
        audience="students",
        priority=70,
        why="School/education days are community+kids events. Often daytime "
            "games for school groups.",
    ),

    # ----- Dog friendly --------------------------------------------------
    Rule(
        id="dog_friendly",
        pattern=_rx(
            r"\bbark (?:in|at) the park\b",
            r"\bbark night\b|\bbark day\b",
            r"\bdog (?:day|night|nite)\b",
            r"\bcanine (?:day|night)\b",
            r"\bpups? (?:at|in) the park\b",
            r"\bpaws at the park\b",
            r"\bk.?9 (?:day|night)\b",
            r"\bpooch .+ park\b",
        ),
        definitive=True,
        flags={"is_dog_friendly": True},
        category="other",
        priority=75,
        why="Dog-friendly events. 'Bark in the Park' is the dominant label "
            "with many small variations.",
    ),

    # ----- Autographs ----------------------------------------------------
    Rule(
        id="autographs",
        pattern=_rx(
            r"\bautograph(?:s)?\b",
            r"\bmeet (?:and|&) greet\b",
            r"\bsigning session\b",
            r"\bpre.?game signing\b|\bpost.?game signing\b",
        ),
        definitive=True,
        flags={"is_autographs": True},
        category="entertainment",
        priority=65,
        why="Autograph/meet-and-greet sessions. Category=entertainment because "
            "the existing schema has no dedicated autograph category.",
    ),

    # ----- Post-game entertainment --------------------------------------
    Rule(
        id="post_game_concert",
        pattern=_rx(
            r"\bpost.?game concert\b",
            r"\bpre.?game concert\b",
            r"\bconcert series\b",
            r"\blive (?:music|band|concert) (?:night|day)\b",
            r"\b(?:drone|laser) show\b",
            r"\bcomedy night\b",
            r"\bmagic (?:show|night)\b",
        ),
        definitive=True,
        flags={"is_entertainment": True},
        category="entertainment",
        priority=75,
        why="Concerts and non-fireworks live entertainment.",
    ),

    # ----- Additions after v1 hit-rate profiling ------------------------
    Rule(
        id="heritage_jackie_robinson",
        pattern=_rx(r"\bjackie robinson\b"),
        definitive=True,
        flags={"is_heritage_night": True, "is_community_event": True},
        category="heritage_night",
        priority=85,
        why="Jackie Robinson Day is an MLB-wide April 15 tribute. Always "
            "heritage + community, recurring annually.",
    ),
    Rule(
        id="community_awareness",
        pattern=_rx(
            r"\bstrikeout cancer\b",
            r"\bcancer awareness\b",
            r"\bbreast cancer\b",
            r"\bmental health (?:night|awareness)\b",
            r"\bautism awareness\b|\bautism acceptance\b",
            r"\balzheimer\w*",
            r"\bsuicide prevention\b",
            r"\bawareness (?:night|day|weekend)\b",
        ),
        definitive=True,
        flags={"is_community_event": True},
        category="community_event",
        priority=75,
        why="Charity/awareness nights. Always community events; often paired "
            "with a cause-marketed jersey auction.",
    ),
    Rule(
        id="community_opening_day",
        pattern=_rx(r"\bopening (?:day|night|weekend)\b"),
        definitive=True,
        flags={"is_community_event": True},
        category="community_event",
        priority=65,
        why="Opening Day is a team-wide community event. Always drawn out "
            "as a special date by every franchise.",
    ),
    Rule(
        id="community_auction",
        pattern=_rx(
            r"\bjersey auction\b",
            r"\bsilent auction\b",
            r"\bcharity auction\b",
            r"\bauction (?:night|day)\b",
        ),
        definitive=True,
        flags={"is_community_event": True},
        category="community_event",
        priority=70,
        why="Auctions are community / charity fundraisers.",
    ),
    Rule(
        id="dog_friendly_v2",
        pattern=_rx(
            r"\bpaws?\s*(?:&|and|\+)\s*claws?\b",
            r"\bpaws? (?:at|in) the park\b",
            r"\bpaws? (?:night|day)\b",
            r"\bwet nose\b",
            r"\bpooch (?:day|night|at)\b",
        ),
        definitive=True,
        flags={"is_dog_friendly": True},
        category="other",
        priority=75,
        why="Additional dog-event phrasings missed by dog_friendly v1 (e.g. "
            "'Paws & Claws', 'Wet Nose Wednesday'). Kept separate from v1 so "
            "each pattern is readable.",
    ),
    Rule(
        id="giveaway_item_standalone",
        pattern=_rx(
            r"\b(?:bobble ?head|jersey|replica jersey|t.?shirt|hat|cap|cap night|"
            r"rally towel|foam finger|mini.?bat|mini.?helmet|blanket)\s+(?:night|day|weekend)\b",
            r"\btheme jersey\b",
            r"\banniversary jersey\b",
            r"\bschedule magnet\b|\bmagnet schedule\b",
        ),
        definitive=True,
        flags={"is_giveaway_item": True},
        category="giveaway",
        priority=80,
        why="Named item + night/day pattern (e.g. 'Hat Night', 'Theme Jersey'). "
            "These are always giveaways even without the word 'giveaway'.",
    ),
    Rule(
        id="kids_appreciation",
        pattern=_rx(
            r"\bscout (?:night|day)\b",
            r"\bboy scouts?\b|\bgirl scouts?\b",
            r"\bcub scouts?\b",
            r"\bprincess (?:day|brunch)\b",
            r"\bsuperhero (?:day|night)\b",
            r"\bteddy bear\b",
        ),
        definitive=True,
        flags={"is_kids_event": True},
        category="kids_event",
        audience="kids",
        priority=70,
        why="Scouts and kid-targeted events. Princess/superhero themed often "
            "overlap with theme_night but the primary intent is kids.",
    ),
    Rule(
        id="food_generic_deal",
        pattern=_rx(
            r"\bdollar days?\b",
            r"\bdollar menu\b",
            r"\bvalue (?:menu|meal)\b",
            r"\bbuck (?:a|an) (?:slice|beer|dog|hot ?dog|taco)\b",
            r"\b(?:1\s*/\s*2|half).?price (?:hot ?dog|beer|wine|taco|burger|"
            r"slice|nacho|wing|soda|food|drink|concession)",
        ),
        definitive=True,
        flags={"is_food_deal": True},
        category="food_deal",
        priority=75,
        why="'Dollar Days', 'Dollar Menu', and half-price food/drink patterns.",
    ),
]


def classify(offer_name: str | None,
             offer_type: str | None = None,
             description: str | None = None) -> dict | None:
    """Attempt a high-confidence rules-based classification.

    Returns a dict shaped like enrich_promotions.sanitize() output (minus
    the `promotion_id`, which the caller attaches), OR None if no
    definitive rule fires. When None is returned, the caller should fall
    through to the LLM.

    IMPORTANT: matching is on offer_name ONLY, NOT offer_type or description.
    Audit showed:
      * offer_type is a coarse API classifier (values like "Giveaway",
        "Theme Days") appearing on thousands of rows regardless of the
        actual event. Matching rules against it caused ~1,800 giveaway
        false-positives (e.g. "Education Day" with offer_type='Giveaway'
        got mis-categorized as giveaway when it's really a school event).
      * description mentions often refer to a supporting perk, not the
        primary promo (e.g. "Strikeout Cancer" + "bobblehead giveaway for
        first 500"). The primary classification should come from the name.

    `offer_type` and `description` args are accepted but ignored so the
    signature matches the LLM-input contract and future rules that
    intentionally consume them can be added explicitly.
    """
    _ = offer_type, description  # intentionally unused -- see docstring
    if not offer_name or not offer_name.strip():
        return None

    text = offer_name.strip()

    matched: list[Rule] = []
    for rule in PROMO_RULES:
        if rule.pattern.search(text):
            matched.append(rule)

    definitive = [r for r in matched if r.definitive]
    if not definitive:
        return None

    # Union the flags
    flags = dict(FLAG_DEFAULTS)
    for rule in matched:
        for k, v in rule.flags.items():
            if k in flags:
                flags[k] = flags[k] or bool(v)

    # Category: highest-priority definitive rule wins. Stable tie-break by
    # list order (first match).
    primary = max(definitive, key=lambda r: r.priority)
    category = primary.category or "other"

    # Audience: first matched rule with a specific audience
    audience = next((r.audience for r in matched if r.audience), "all")

    # Giveaway limit extraction (orthogonal to rule matching)
    limit = _extract_giveaway_limit(text)

    rule_ids = [r.id for r in matched]

    return {
        "promo_category":     category,
        **flags,
        "giveaway_limit":     limit,
        "target_audience":    audience,
        "llm_notes":          None,
        "rules_matched":      rule_ids,  # for audit; NOT written to main table
    }


_LIMIT_RX = re.compile(
    r"first\s+([\d,]{1,6})\s+(?:fans?|kids?|children|attendees|guests?)",
    re.IGNORECASE,
)


def _extract_giveaway_limit(text: str) -> int | None:
    m = _LIMIT_RX.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ----- Self-tests ---------------------------------------------------------
# These are wired as a tiny __main__ harness so running the module directly
# gives a quick sanity check. Not a substitute for the audit script but
# handy during rule development.

if __name__ == "__main__":
    cases = [
        ("Fireworks Friday", None, None),
        ("Post-game Fireworks", None, None),
        ("Kids Run the Bases", None, None),
        ("Bark in the Park", None, None),
        ("Bobblehead Giveaway: Star Player", None, None),
        ("$2 Hot Dog Tuesday", None, None),
        ("Thirsty Thursday", None, None),
        ("Taco Tuesday", None, None),
        ("White Claw Wednesday", None, None),
        ("$2 Tuesday", None, None),
        ("Star Wars Night", None, None),
        ("Military Appreciation Night", None, None),
        ("Pride Night", None, None),
        ("Hispanic Heritage Night", None, None),
        ("Education Day", None, None),
        ("Family Funday", None, None),
        ("Twisted Tuesday", None, None),          # ambiguous -> None
        ("Sunday Funday", None, None),            # ambiguous -> None
        ("Silver Sluggers", None, None),          # ambiguous -> None
        ("Post-game concert with local band", None, None),
        ("First 1000 fans get a bobblehead", None, None),
    ]
    for name, otype, desc in cases:
        out = classify(name, otype, desc)
        if out is None:
            print(f"  [LLM]       {name}")
        else:
            flags = [k for k, v in out.items() if k.startswith("is_") or k == "has_celebrity" if v]
            print(f"  [{out['promo_category']:>11}] {name:<40} flags={flags} aud={out['target_audience']} limit={out['giveaway_limit']}")
