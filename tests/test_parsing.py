from dragonwilds import parsing

RUN = [
    "[2026.10.14-19.53.18:912][  0]LogDomServerGameMode: Loading configured world [MyWorld]"
    " from save slot [MyWorld]",
    '[2026.10.14-19.53.23:396][120]LogNetSessionSettings: Setting ["WorldSaveGuid"] written with'
    " key[xg] value[0123456789ABCDEF0123456789ABCDEF]",
    '[2026.10.14-19.53.23:396][120]LogNetSessionSettings: Setting ["JoinCode"] written with'
    " key[xz] value[OLD0-CODE]",
    "[2026.10.14-19.58.20:953][852]LogPersistence: [DedicatedServer] operator()() : Save completed"
    " SUCCESSFULLY (slot: MyWorld)",
    '[2026.10.14-20.01.02:000][  1]LogNetSessionSettings: Setting ["JoinCode"] written with'
    " key[xz] value[AAAA-1111]",
]


def login(name: str, user: str) -> str:
    return f"LogNet: Login request: ?pf=PC?cpx=1?c?Name={name} userId: {user} platform: RedpointEOS"


def left(user: str) -> str:
    return f"LogNet: UChannel::CleanUp: ChIndex == 0. Closing connection. ... UniqueId: {user}"


def test_reads_the_world_and_the_latest_invite_code():
    assert parsing.last_match(parsing.WORLD_NAME, RUN) == "MyWorld"
    assert parsing.last_match(parsing.WORLD_GUID, RUN) == "0123456789ABCDEF0123456789ABCDEF"
    assert parsing.last_match(parsing.JOIN_CODE, RUN) == "AAAA-1111"
    assert parsing.last_match(parsing.JOIN_CODE, []) is None


def test_online_players_replays_joins_and_disconnects():
    lines = [
        login("Alice", "RedpointEOS:a"),
        "LogNet: Join succeeded: Alice",
        login("Big Bob", "RedpointEOS:b"),
        "LogNet: Join succeeded: Big Bob",
    ]
    assert parsing.online_players(lines) == ["Alice", "Big Bob"]
    lines.append(left("RedpointEOS:a"))
    assert parsing.online_players(lines) == ["Big Bob"]
    lines.append("LogDomGameMode: Requested pausing as we have no player connected")
    assert parsing.online_players(lines) == []


def test_build_ids():
    assert parsing.acf_build_id('\t"buildid"\t\t"25811348"') == "25811348"
    assert parsing.acf_build_id("") is None
    steamcmd = '\x1b[0mInstalled Steam build ID: "25811348"'
    assert parsing.last_match(parsing.INSTALLED_BUILD, [steamcmd]) == "25811348"
    payload = {"data": {"4019830": {"depots": {"branches": {"public": {"buildid": "25811348"}}}}}}
    assert parsing.public_build_id(payload, "4019830") == "25811348"
