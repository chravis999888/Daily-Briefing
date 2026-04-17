import os
import shutil
import time
from pathlib import Path

from memory import (load_memory, save_memory, load_pinned, load_health, save_health, log_run,
                    get_cached_category, get_previous_stories, save_today_stories,
                    save_article_hash, category_has_changed, detect_developing_situations)
from fetchers import (fetch_gdelt_articles, fetch_guardian, fetch_rss, fetch_newsdata)
from processors import (process_breaking_news, process_australia, process_archaeology,
                        process_football, process_world_topics, process_developing_situations)
from page.builder import build_html

MOCK_MODE = False
RUN_MODE = os.environ.get("RUN_MODE", "full")
RUN_CATEGORY = os.environ.get("RUN_CATEGORY", "")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWSDATA_KEY = os.environ.get("NEWSDATA_API_KEY", "")
GUARDIAN_KEY = os.environ.get("GUARDIAN_API_KEY", "")

if not MOCK_MODE:
    if not ANTHROPIC_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set")
    if not NEWSDATA_KEY:
        raise EnvironmentError("NEWSDATA_API_KEY not set")
    if not GUARDIAN_KEY:
        raise EnvironmentError("GUARDIAN_API_KEY not set")

FAVICON_FILES = ["favicon.ico", "favicon.svg", "favicon-32.png", "favicon-16.png", "apple-touch-icon.png", "logo.svg"]


def _copy_favicons():
    for fname in FAVICON_FILES:
        src = Path(fname)
        if src.exists():
            shutil.copy(src, Path("dist") / fname)


def mock_data():
    all_data = {
        "breaking": [
            {
                "headline": "Russian forces launch largest missile barrage of the war, striking Kyiv and 6 other cities simultaneously with 180 drones and 40 cruise missiles",
                "score": 9, "timestamp": "2 hrs ago",
                "summary": "Russia launched its largest coordinated missile attack of the conflict overnight, firing 180 Shahed drones and 40 cruise missiles at Ukrainian cities. Ukrainian air defences intercepted around 130 projectiles but at least 40 struck targets in Kyiv, Kharkiv, Zaporizhzhia, Dnipro and three other cities. At least 23 civilians were killed and 91 injured. The strikes targeted energy infrastructure, knocking out power to 1.4 million homes. The attack came hours after peace talks in Istanbul were suspended without agreement.",
                "image": "https://picsum.photos/seed/war/780/440",
                "articles": [
                    {"title": "Russia fires record 180 drones and 40 cruise missiles at Ukraine overnight", "source": "The Guardian", "url": "https://theguardian.com"},
                    {"title": "Ukraine says 23 dead after Russia's largest ever missile attack", "source": "Reuters", "url": "https://reuters.com"},
                    {"title": "Istanbul peace talks collapse hours before missile barrage", "source": "BBC News", "url": "https://bbc.com"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "7.8-magnitude earthquake strikes southern Turkey near Syrian border, 340 confirmed dead as rescuers search collapsed buildings",
                "score": 8, "timestamp": "4 hrs ago",
                "summary": "A powerful 7.8-magnitude earthquake struck Hatay province in southern Turkey at 3:14 AM local time, collapsing hundreds of buildings and killing at least 340 people. The quake was felt across Lebanon, Syria and Cyprus. Turkish emergency services and international rescue teams have deployed to the region. The same area was devastated by a catastrophic earthquake in February 2023 that killed over 50,000 people.",
                "image": "https://picsum.photos/seed/quake/780/440",
                "articles": [
                    {"title": "Earthquake kills 340 in Turkey's Hatay province, thousands missing", "source": "AP", "url": "https://apnews.com"},
                    {"title": "Turkey earthquake: rescuers race to pull survivors from rubble", "source": "BBC News", "url": "https://bbc.com"},
                    {"title": "Same region hit by 2023 disaster faces renewed catastrophe", "source": "Al Jazeera", "url": "https://aljazeera.com"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "North Korea fires three ballistic missiles into Sea of Japan, US and South Korea scramble jets in response",
                "score": 7, "timestamp": "6 hrs ago",
                "summary": "North Korea launched three short-range ballistic missiles from the Sunan area near Pyongyang early Saturday morning, all landing in the Sea of Japan within Japan's exclusive economic zone. The launches came one day after the US and South Korea concluded joint naval exercises in the region. South Korea's Joint Chiefs of Staff condemned the launches and the US Indo-Pacific Command issued a statement calling the launches destabilising.",
                "image": "",
                "articles": [
                    {"title": "North Korea fires three ballistic missiles toward Japan", "source": "Reuters", "url": "https://reuters.com"},
                    {"title": "UN Security Council to hold emergency session over DPRK launches", "source": "The Guardian", "url": "https://theguardian.com"},
                ],
                "tracking_suggestions": []
            },
        ],
        "australia": [
            {
                "headline": "Senate passes $14.6bn housing bill after Greens withdraw opposition in exchange for social housing funding boost",
                "score": 8, "timestamp": "3 hrs ago",
                "summary": "The Albanese government's flagship housing legislation passed the Senate 36-34 after the Greens agreed to support the bill following a last-minute deal that increases social housing funding by $1.2 billion. The Help to Buy scheme will allow 40,000 Australians per year to purchase homes with a government equity contribution of up to 40 percent. The Coalition opposed the bill, arguing it will inflate house prices. Housing Minister Clare O'Neil called it the most significant federal housing intervention in a generation.",
                "image": "",
                "articles": [
                    {"title": "Help to Buy housing bill passes Senate after Greens do deal", "source": "ABC News", "url": "https://abc.net.au"},
                    {"title": "Greens secure $1.2bn social housing boost in exchange for housing vote", "source": "SMH", "url": "https://smh.com.au"},
                    {"title": "Opposition slams housing scheme as inflationary after Senate defeat", "source": "The Australian", "url": "https://theaustralian.com.au"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "High Court rules NSW government's koala protection policy unconstitutional, opening 2.3 million hectares to logging",
                "score": 7, "timestamp": "5 hrs ago",
                "summary": "Australia's High Court voted 5-2 to strike down New South Wales' koala habitat protection overlays, finding they exceeded state environmental planning powers. The ruling potentially reopens 2.3 million hectares of coastal forest to logging that had been protected since 2021. Environmental groups called it a catastrophic setback while the timber industry welcomed the decision. The Minns government said it would introduce new legislation within 60 days to restore protections.",
                "image": "",
                "articles": [
                    {"title": "High Court strikes down NSW koala habitat protections in 5-2 ruling", "source": "SMH", "url": "https://smh.com.au"},
                    {"title": "Minns government promises new koala legislation within 60 days", "source": "ABC News", "url": "https://abc.net.au"},
                ],
                "tracking_suggestions": []
            },
        ],
        "archaeology": [
            {
                "headline": "750,000-year-old stone tools found in Philippines challenge theory that only Homo erectus reached island Southeast Asia this early",
                "score": 9, "timestamp": "1 day ago",
                "summary": "Archaeologists excavating Luzon's Cagayan Valley have uncovered 754 stone tools dated to approximately 750,000 years ago using argon-argon and paleomagnetic dating methods. The tools pre-date the oldest known fossils of Homo luzonensis by 600,000 years and are far too early to be attributed to modern humans or Denisovans. The find suggests an unknown hominin species capable of crossing open water reached the Philippine archipelago during the Early Pleistocene, upending current models of early human dispersal in Southeast Asia.",
                "image": "",
                "articles": [
                    {"title": "Stone tools push back human presence in Philippines by 200,000 years", "source": "Nature", "url": "https://nature.com"},
                    {"title": "Mystery hominin crossed open ocean to reach Philippines 750,000 years ago", "source": "New Scientist", "url": "https://newscientist.com"},
                    {"title": "Cagayan Valley dig upends Southeast Asian prehistory", "source": "Science", "url": "https://science.org"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "Ancient DNA from 6,000-year-old Irish megalith reveals first-cousin marriage among Neolithic elites and a distinct genetic lineage that vanished",
                "score": 7, "timestamp": "2 days ago",
                "summary": "Genomic analysis of 36 individuals buried in the Newgrange passage tomb between 3200 and 2900 BCE shows that the central burial belonged to a man whose parents were first-degree relatives — most likely a brother and sister — indicating deliberate elite inbreeding similar to later Egyptian pharaohs and Inca rulers. The study also identified a distinct Neolithic genetic lineage with no detectable ancestry in modern Europeans, suggesting this population was largely replaced during the Bronze Age Steppe migration.",
                "image": "",
                "articles": [
                    {"title": "Newgrange tomb DNA reveals incest and a lost European lineage", "source": "Science", "url": "https://science.org"},
                    {"title": "Ireland's Neolithic elites practised deliberate sibling marriage, genome study finds", "source": "Nature", "url": "https://nature.com"},
                ],
                "tracking_suggestions": []
            },
        ],
        "football": [
            {
                "headline": "Arsenal beat Manchester City 2-1 at the Etihad to go top of Premier League on goal difference with 4 games remaining",
                "score": 9, "timestamp": "yesterday",
                "summary": "Arsenal claimed a crucial victory at the Etihad Stadium, with Bukayo Saka scoring an 87th-minute winner after Martin Odegaard's opener was cancelled out by Erling Haaland's equaliser. The result puts Arsenal level on points with City at the top of the Premier League table but ahead on goal difference with four matches remaining. It is Arsenal's first win at the Etihad in 9 attempts across all competitions.",
                "image": "",
                "articles": [
                    {"title": "Saka 87th-minute winner sends Arsenal top as City suffer title blow", "source": "The Guardian", "url": "https://theguardian.com/football"},
                    {"title": "Manchester City 1-2 Arsenal: Haaland equaliser not enough as Saka clinches it", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                    {"title": "Arsenal go top on goal difference with four games to play", "source": "Sky Sports", "url": "https://skysports.com"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "Real Madrid eliminate Bayern Munich 3-2 on aggregate to reach Champions League final, Vinicius Jr scores twice in second leg",
                "score": 8, "timestamp": "yesterday",
                "summary": "Real Madrid reached their fifth Champions League final in ten years after Vinicius Jr scored twice in a 2-1 second-leg win over Bayern Munich at the Bernabeu. Harry Kane pulled one back for Bayern in the 78th minute to set up a tense finish but Madrid held on. They will face Inter Milan in the final in Istanbul on June 1st. It is the 18th Champions League final in Real Madrid's history.",
                "image": "",
                "articles": [
                    {"title": "Vinicius double sends Real Madrid to Istanbul final", "source": "Marca", "url": "https://marca.com"},
                    {"title": "Real Madrid 2-1 Bayern Munich (3-2 agg): player ratings", "source": "The Guardian", "url": "https://theguardian.com/football"},
                    {"title": "Real Madrid vs Inter Milan: Champions League final preview", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "Lamine Yamal becomes youngest player in La Liga history to reach 20 assists in a season at age 17",
                "score": 7, "timestamp": "8 hrs ago",
                "summary": "Barcelona's Lamine Yamal set up two goals in Saturday's 3-0 win over Getafe to take his La Liga assist tally to 20 for the season, breaking the record previously held by Lionel Messi set in 2009. The 17-year-old has also scored 16 goals this campaign. Barcelona manager Hansi Flick called it a historic achievement for the youngest player to ever represent Spain at a major tournament.",
                "image": "",
                "articles": [
                    {"title": "Yamal breaks Messi's La Liga assist record at 17", "source": "Marca", "url": "https://marca.com"},
                    {"title": "Barcelona 3-0 Getafe: Yamal two assists as Barca cruise", "source": "ESPN", "url": "https://espn.com"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "Nottingham Forest relegated from Premier League after 1-0 defeat to Everton leaves them 18th with one match left",
                "score": 7, "timestamp": "3 hrs ago",
                "summary": "Nottingham Forest were relegated from the Premier League after a 1-0 home defeat to Everton. Dominic Calvert-Lewin's 54th-minute header proved decisive. Forest remain 18th with 31 points and cannot mathematically escape the bottom three. It ends a three-year stay in the top flight for the club.",
                "image": "",
                "articles": [
                    {"title": "Nottingham Forest relegated as Everton win at the City Ground", "source": "Sky Sports", "url": "https://skysports.com"},
                    {"title": "Calvert-Lewin header condemns Forest to Championship", "source": "BBC Sport", "url": "https://bbc.com/sport"},
                ],
                "tracking_suggestions": []
            },
            {
                "headline": "PSG win Ligue 1 title for 12th time despite drawing 1-1 with Rennes; Monaco's win elsewhere not enough",
                "score": 6, "timestamp": "2 hrs ago",
                "summary": "Paris Saint-Germain were confirmed as Ligue 1 champions for the twelfth time after drawing 1-1 at Rennes while Monaco beat Lyon 2-0 but could not close the four-point gap. It is PSG's first title without Kylian Mbappe, who left for Real Madrid last summer. Manager Luis Enrique praised the squad's resilience following a difficult transitional season.",
                "image": "",
                "articles": [
                    {"title": "PSG crowned Ligue 1 champions for record 12th time", "source": "L'Equipe", "url": "https://lequipe.fr"},
                    {"title": "First title without Mbappe caps Luis Enrique's debut season in Paris", "source": "The Guardian", "url": "https://theguardian.com/football"},
                ],
                "tracking_suggestions": []
            },
        ]
    }

    world_topics = {
        "today": [
            {"headline": "Ukraine-Russia peace talks collapse in Istanbul", "why": "Negotiations broke down after Russia refused to withdraw from occupied territories, raising fears of further escalation.", "signal": "both sources"},
            {"headline": "US tariffs on Chinese goods raised to 145%", "why": "The White House announced a new round of tariff increases, sending global markets into sharp decline.", "signal": "both sources"},
            {"headline": "Turkey earthquake rescue operations ongoing", "why": "Hundreds confirmed dead after a 7.8-magnitude quake near the Syrian border with thousands still missing.", "signal": "reddit only"},
            {"headline": "North Korea missile launches condemned by G7", "why": "Three ballistic missiles fired into the Sea of Japan triggered an emergency UN Security Council session.", "signal": "trends only"},
            {"headline": "OpenAI releases GPT-5 to general public", "why": "The new model scores above human level on all major benchmarks, sparking widespread debate about AI timelines.", "signal": "both sources"},
        ],
        "week": [
            {"headline": "Global ceasefire negotiations in multiple conflicts", "why": "Simultaneous diplomatic pushes in Ukraine, Gaza and Sudan dominated international headlines all week.", "signal": "trending for 6 days"},
            {"headline": "US Federal Reserve holds rates amid inflation data", "why": "Markets were volatile as the Fed signalled no cuts before Q3, disappointing investors expecting relief.", "signal": "trending for 5 days"},
            {"headline": "Apple WWDC announcements", "why": "Apple revealed sweeping AI integration across all platforms, with on-device models replacing Siri.", "signal": "trending for 4 days"},
            {"headline": "Champions League semi-finals", "why": "High-drama second legs across all four ties kept football dominating social media throughout the week.", "signal": "trending for 7 days"},
            {"headline": "Measles outbreak spreads across US states", "why": "CDC declared a public health emergency as cases reached a 30-year high following vaccine hesitancy campaigns.", "signal": "trending for 3 days"},
        ],
        "month": [
            {"headline": "US-China trade war escalation", "why": "The tariff spiral dominated economic coverage for the entire month as recession fears grew globally.", "signal": "trending for 28 days"},
            {"headline": "Gaza ceasefire negotiations", "why": "Multiple rounds of talks mediated by Qatar and Egypt kept the conflict at the top of global news agendas.", "signal": "trending for 25 days"},
            {"headline": "AI regulation bills advancing in US and EU", "why": "Landmark legislation moving through both US Congress and the European Parliament attracted sustained attention.", "signal": "trending for 18 days"},
            {"headline": "Premier League title race", "why": "The tightest title race in a decade between Arsenal and Manchester City ran across every week of the month.", "signal": "trending for 30 days"},
            {"headline": "Climate records shattered globally", "why": "April 2026 became the hottest April ever recorded, extending a 13-month streak of record-breaking temperatures.", "signal": "trending for 22 days"},
        ]
    }

    developing_situations = [
        {
            "topic": "Ukraine war",
            "type": "pinned",
            "update": "Russia's overnight missile barrage was the largest of the conflict, striking 7 cities with 180 drones and 40 cruise missiles. 23 civilians confirmed dead. Peace talks in Istanbul suspended without agreement earlier the same day.",
            "has_update": True,
            "articles": [
                {"title": "Russia launches record missile barrage at Ukraine", "source": "The Guardian", "url": "https://theguardian.com"},
                {"title": "Istanbul talks collapse as Russia rejects withdrawal terms", "source": "Reuters", "url": "https://reuters.com"},
                {"title": "Ukraine air defences intercept 130 of 220 projectiles", "source": "BBC", "url": "https://bbc.com"},
            ]
        },
        {
            "topic": "Gaza ceasefire talks",
            "type": "pinned",
            "update": "Qatar-mediated negotiations continue in Doha with both sides represented. A new framework proposal involving a 60-day pause and hostage release is reportedly on the table but Hamas has not yet formally responded.",
            "has_update": True,
            "articles": [
                {"title": "Qatar hosts new round of Gaza ceasefire talks", "source": "Al Jazeera", "url": "https://aljazeera.com"},
                {"title": "60-day pause proposal outline published", "source": "Haaretz", "url": "https://haaretz.com"},
            ]
        },
        {
            "topic": "US-China trade war",
            "type": "auto",
            "update": "No significant updates today beyond market reactions to the new 145% tariff announcement. Beijing has scheduled a press conference for Monday.",
            "has_update": False,
            "articles": []
        },
    ]

    yesterday_data = {
        "breaking": [
            {"headline": "Israeli airstrike on Rafah kills 34, Palestinian health ministry reports", "score": 8, "timestamp": "yesterday", "summary": "", "url": "", "image": "", "articles": [], "tracking_suggestions": []},
        ],
        "australia": [
            {"headline": "RBA holds cash rate at 4.1% for fifth consecutive meeting despite falling inflation", "score": 7, "timestamp": "yesterday", "summary": "", "url": "", "image": "", "articles": [], "tracking_suggestions": []},
        ],
        "archaeology": [],
        "football": [
            {"headline": "Manchester United sack Ruben Amorim after 5 consecutive Premier League defeats, club 14th", "score": 8, "timestamp": "yesterday", "summary": "", "url": "", "image": "", "articles": [], "tracking_suggestions": []},
        ],
    }

    return all_data, yesterday_data, world_topics, developing_situations


def main():
    if MOCK_MODE:
        print("MOCK_MODE enabled — skipping all API calls.")
        all_data, yesterday_data, world_topics, developing_situations = mock_data()
        Path("dist").mkdir(exist_ok=True)
        _copy_favicons()
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations))
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written.")
        return

    memory = load_memory()
    pinned = load_pinned()
    health = load_health()

    if RUN_MODE == "deploy_only":
        print("Deploy-only run — rebuilding HTML from cache, zero API calls.")
        errors = []
        all_data = {cat: get_cached_category(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []})
        developing_situations = process_developing_situations(pinned, [], [])
        health = log_run(health, "deploy_only", errors)
        save_health(health)
        Path("dist").mkdir(exist_ok=True)
        _copy_favicons()
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written from cache.")
        return

    if RUN_MODE == "breaking_only":
        print("Breaking-only run...")
        errors = []
        content_changed = False
        gdelt_breaking, gdelt_err, memory = fetch_gdelt_articles("war attack disaster killed", timespan="1h", max_records=25, memory=memory)
        if not isinstance(memory, dict):
            print(f"ERROR: memory corrupted after GDELT call (got {type(memory)}), reloading from disk")
            memory = load_memory()
        if gdelt_err:
            print(f"GDELT: {gdelt_err}")
            if "skipped" not in gdelt_err:
                errors.append(gdelt_err)
        guardian_breaking = fetch_guardian("world war attack disaster crisis killed invasion", page_size=15)
        reuters_rss = fetch_rss("https://feeds.reuters.com/reuters/topNews", "Reuters")
        ap_rss = fetch_rss("https://rsshub.app/apnews/topics/apf-topnews", "AP News")
        bbc_rss = fetch_rss("https://feeds.bbci.co.uk/news/rss.xml", "BBC News")
        aljazeera_rss = fetch_rss("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera")
        all_breaking = gdelt_breaking + guardian_breaking + reuters_rss + ap_rss + bbc_rss + aljazeera_rss

        if category_has_changed(memory, "breaking", all_breaking):
            new_breaking, memory = process_breaking_news([], all_breaking, memory)
            if new_breaking:
                breaking = new_breaking
                content_changed = True
            else:
                print("Breaking news: articles changed but nothing passed the bar, keeping existing")
                breaking = get_cached_category(memory, "breaking")
            memory = save_article_hash(memory, "breaking", all_breaking)
        else:
            print("Breaking news: no new articles since last check, skipping Sonnet call")
            breaking = get_cached_category(memory, "breaking")

        memory = save_today_stories(memory, "breaking", breaking)

        all_data = {
            "breaking": breaking,
            "australia": get_cached_category(memory, "australia"),
            "archaeology": get_cached_category(memory, "archaeology"),
            "football": get_cached_category(memory, "football")
        }
        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []})
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        developing_situations = process_developing_situations(pinned, [], all_breaking) if pinned else []

        save_memory(memory)
        health = log_run(health, "breaking_only", errors)
        save_health(health)

        Path("dist").mkdir(exist_ok=True)
        _copy_favicons()
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        if content_changed:
            Path("dist/.deploy_needed").touch()
            print("Done. dist/index.html written — deploy triggered.")
        else:
            print("Done. dist/index.html written — no new content, deploy skipped.")
        return

    elif RUN_MODE == "category" and RUN_CATEGORY:
        print(f"Category-only run: {RUN_CATEGORY}...")
        errors = []
        content_changed = False

        if RUN_CATEGORY == "football":
            guardian_football = fetch_guardian("premier league OR la liga OR serie a OR bundesliga OR ligue 1 OR champions league", page_size=15, section="football")
            marca_rss = fetch_rss("https://e00-marca.uecdn.es/rss/futbol/primera-division.xml", "Marca")
            kicker_rss = fetch_rss("https://newsfeed.kicker.de/news/fussball", "Kicker")
            lequipe_rss = fetch_rss("https://www.lequipe.fr/rss/actu_rss_Football.xml", "L'Equipe")
            gazzetta_rss = fetch_rss("https://www.gazzetta.it/rss/home.xml", "Gazzetta dello Sport")
            sky_rss = fetch_rss("https://www.skysports.com/rss/12040", "Sky Sports")
            espn_rss = fetch_rss("https://www.espn.com/espn/rss/soccer/news", "ESPN FC")
            bbc_football_rss = fetch_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport")
            football_italia_rss = fetch_rss("https://www.football-italia.net/rss.xml", "Football Italia")
            bundesliga_rss = fetch_rss("https://www.bundesliga.com/api/rss/news/en", "Bundesliga")
            uefa_rss = fetch_rss("https://www.uefa.com/rss.xml", "UEFA")
            goal_rss = fetch_rss("https://www.goal.com/feeds/en/news", "Goal.com")
            articles = (guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
                        espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)[:40]
            if category_has_changed(memory, "football", articles):
                result, memory = process_football(articles, memory)
                memory = save_article_hash(memory, "football", articles)
                content_changed = True
            else:
                print("Football: no new articles, skipping")
                result = get_cached_category(memory, "football")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": result
            }

        elif RUN_CATEGORY == "australia":
            abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
            smh_rss = fetch_rss("https://www.smh.com.au/rss/feed.xml", "SMH")
            age_rss = fetch_rss("https://www.theage.com.au/rss/feed.xml", "The Age")
            newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
            articles = abc_rss + smh_rss + age_rss + newsdata_aus
            if category_has_changed(memory, "australia", articles):
                result, memory = process_australia(abc_rss + smh_rss + age_rss, newsdata_aus, memory)
                memory = save_article_hash(memory, "australia", articles)
                content_changed = True
            else:
                print("Australia: no new articles, skipping")
                result = get_cached_category(memory, "australia")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": result,
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": get_cached_category(memory, "football")
            }

        elif RUN_CATEGORY == "archaeology":
            nature_rss = fetch_rss("https://www.nature.com/nature.rss", "Nature")
            newscientist_rss = fetch_rss("https://www.newscientist.com/subject/humans/feed/", "New Scientist")
            science_rss = fetch_rss("https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science", "Science")
            newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
            physorg_rss = fetch_rss("https://phys.org/rss-feed/biology-news/evolution/", "PhysOrg")
            eurekalert_rss = fetch_rss("https://www.eurekalert.org/rss/all.xml", "EurekAlert")
            sciencedaily_rss = fetch_rss("https://www.sciencedaily.com/rss/fossils_ruins/human_evolution.xml", "ScienceDaily")
            conversation_rss = fetch_rss("https://theconversation.com/us/science/rss", "The Conversation")
            articles = (nature_rss + newscientist_rss + science_rss + newsdata_arch +
                        physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss)
            if category_has_changed(memory, "archaeology", articles):
                result, memory = process_archaeology(articles, memory)
                memory = save_article_hash(memory, "archaeology", articles)
                content_changed = True
            else:
                print("Archaeology: no new articles, skipping")
                result = get_cached_category(memory, "archaeology")
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": result,
                "football": get_cached_category(memory, "football")
            }

        elif RUN_CATEGORY == "world_topics":
            world_topics, memory = process_world_topics(memory)
            content_changed = True
            all_data = {
                "breaking": get_cached_category(memory, "breaking"),
                "australia": get_cached_category(memory, "australia"),
                "archaeology": get_cached_category(memory, "archaeology"),
                "football": get_cached_category(memory, "football")
            }
        else:
            print(f"Unknown category: {RUN_CATEGORY}, aborting.")
            return

        world_topics = memory.get("world_topics_cache", {"today": [], "week": [], "month": []}) if RUN_CATEGORY != "world_topics" else world_topics
        yesterday_data = {cat: get_previous_stories(memory, cat) for cat in ["breaking", "australia", "archaeology", "football"]}
        developing_situations = process_developing_situations(pinned, [], [])
        if RUN_CATEGORY in ("breaking", "australia", "archaeology", "football"):
            memory = save_today_stories(memory, RUN_CATEGORY, result)
        save_memory(memory)
        health = log_run(health, f"category:{RUN_CATEGORY}", errors)
        save_health(health)
        Path("dist").mkdir(exist_ok=True)
        _copy_favicons()
        with open("dist/index.html", "w", encoding="utf-8") as f:
            f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
        if content_changed:
            Path("dist/.deploy_needed").touch()
            print(f"Done. Category-only run for {RUN_CATEGORY} complete — deploy triggered.")
        else:
            print(f"Done. Category-only run for {RUN_CATEGORY} complete — no new content, deploy skipped.")
        return

    # Full run
    errors = []
    content_changed = False

    print("Fetching world topics...")
    world_topics, memory = process_world_topics(memory)

    print("Fetching Breaking News...")
    gdelt_breaking, gdelt_err, memory = fetch_gdelt_articles("war killed attack invasion disaster explosion casualties", timespan="1h", max_records=25, memory=memory)
    if not isinstance(memory, dict):
        print(f"ERROR: memory corrupted after GDELT call (got {type(memory)}), reloading from disk")
        memory = load_memory()
    if gdelt_err:
        print(f"GDELT: {gdelt_err}")
        if "skipped" not in gdelt_err:
            errors.append(gdelt_err)
    guardian_breaking = fetch_guardian("world war attack disaster crisis killed invasion", page_size=15)
    reuters_rss = fetch_rss("https://feeds.reuters.com/reuters/topNews", "Reuters")
    ap_rss = fetch_rss("https://rsshub.app/apnews/topics/apf-topnews", "AP News")
    bbc_rss = fetch_rss("https://feeds.bbci.co.uk/news/rss.xml", "BBC News")
    aljazeera_rss = fetch_rss("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera")
    all_breaking = gdelt_breaking + guardian_breaking + reuters_rss + ap_rss + bbc_rss + aljazeera_rss
    new_breaking, memory = process_breaking_news([], all_breaking, memory)
    if new_breaking:
        breaking = new_breaking
        memory = save_article_hash(memory, "breaking", all_breaking)
    else:
        print("Breaking news: no new stories passed the bar, keeping existing")
        breaking = get_cached_category(memory, "breaking")

    time.sleep(60)
    print("Fetching Australia news...")
    abc_rss = fetch_rss("https://www.abc.net.au/news/feed/51120/rss.xml", "ABC News")
    smh_rss = fetch_rss("https://www.smh.com.au/rss/feed.xml", "SMH")
    age_rss = fetch_rss("https://www.theage.com.au/rss/feed.xml", "The Age")
    newsdata_aus = fetch_newsdata("australia parliament senate election albanese budget policy", country="au")
    australia, memory = process_australia(abc_rss + smh_rss + age_rss, newsdata_aus, memory)

    time.sleep(60)
    print("Fetching Archaeology news...")
    nature_rss = fetch_rss("https://www.nature.com/nature.rss", "Nature")
    newscientist_rss = fetch_rss("https://www.newscientist.com/subject/humans/feed/", "New Scientist")
    science_rss = fetch_rss("https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science", "Science")
    newsdata_arch = fetch_newsdata("paleoanthropology fossil hominin ancient DNA homo sapiens neanderthal discovery")
    physorg_rss = fetch_rss("https://phys.org/rss-feed/biology-news/evolution/", "PhysOrg")
    eurekalert_rss = fetch_rss("https://www.eurekalert.org/rss/all.xml", "EurekAlert")
    sciencedaily_rss = fetch_rss("https://www.sciencedaily.com/rss/fossils_ruins/human_evolution.xml", "ScienceDaily")
    conversation_rss = fetch_rss("https://theconversation.com/us/science/rss", "The Conversation")
    archaeology, memory = process_archaeology(
        nature_rss + newscientist_rss + science_rss + newsdata_arch +
        physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss, memory)

    time.sleep(60)
    print("Fetching Football news...")
    guardian_football = fetch_guardian(
        "premier league OR la liga OR serie a OR bundesliga OR ligue 1 OR champions league",
        page_size=15, section="football"
    )
    marca_rss = fetch_rss("https://e00-marca.uecdn.es/rss/futbol/primera-division.xml", "Marca")
    kicker_rss = fetch_rss("https://newsfeed.kicker.de/news/fussball", "Kicker")
    lequipe_rss = fetch_rss("https://www.lequipe.fr/rss/actu_rss_Football.xml", "L'Equipe")
    gazzetta_rss = fetch_rss("https://www.gazzetta.it/rss/home.xml", "Gazzetta dello Sport")
    sky_rss = fetch_rss("https://www.skysports.com/rss/12040", "Sky Sports")
    espn_rss = fetch_rss("https://www.espn.com/espn/rss/soccer/news", "ESPN FC")
    bbc_football_rss = fetch_rss("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport")
    football_italia_rss = fetch_rss("https://www.football-italia.net/rss.xml", "Football Italia")
    bundesliga_rss = fetch_rss("https://www.bundesliga.com/api/rss/news/en", "Bundesliga")
    uefa_rss = fetch_rss("https://www.uefa.com/rss.xml", "UEFA")
    goal_rss = fetch_rss("https://www.goal.com/feeds/en/news", "Goal.com")
    football, memory = process_football(
        (guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
        espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)[:40],
        memory)

    all_data = {
        "breaking": breaking,
        "australia": australia,
        "archaeology": archaeology,
        "football": football
    }

    print("Processing developing situations...")
    all_fetched = (all_breaking + abc_rss + smh_rss + age_rss +
                   newsdata_aus + nature_rss + newscientist_rss + science_rss + newsdata_arch +
                   physorg_rss + eurekalert_rss + sciencedaily_rss + conversation_rss +
                   guardian_football + marca_rss + kicker_rss + lequipe_rss + gazzetta_rss + sky_rss +
                   espn_rss + bbc_football_rss + football_italia_rss + bundesliga_rss + uefa_rss + goal_rss)
    auto_detected = detect_developing_situations(memory, all_data)
    developing_situations = process_developing_situations(pinned, auto_detected, all_fetched)

    yesterday_data = {
        "breaking": get_previous_stories(memory, "breaking"),
        "australia": get_previous_stories(memory, "australia"),
        "archaeology": get_previous_stories(memory, "archaeology"),
        "football": get_previous_stories(memory, "football")
    }

    for cat in ["breaking", "australia", "archaeology", "football"]:
        memory = save_today_stories(memory, cat, all_data[cat])
    content_changed = any(all_data[cat] for cat in ["breaking", "australia", "archaeology", "football"])
    save_memory(memory)
    health = log_run(health, "full", errors)
    save_health(health)

    Path("dist").mkdir(exist_ok=True)
    _copy_favicons()
    with open("dist/index.html", "w", encoding="utf-8") as f:
        f.write(build_html(all_data, yesterday_data, world_topics, developing_situations, health=health))
    if content_changed:
        Path("dist/.deploy_needed").touch()
        print("Done. dist/index.html written — deploy triggered.")
    else:
        print("Done. dist/index.html written — no new content, deploy skipped.")


if __name__ == "__main__":
    main()
