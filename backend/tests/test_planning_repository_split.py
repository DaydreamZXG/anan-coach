from __future__ import annotations

import ast
from collections import Counter
from hashlib import sha256
import inspect
from pathlib import Path

from app.domains.planning import repository as facade
from app.domains.planning.errors import PlanningContractError
from app.domains.planning.repository import PlanningRepository
from app.domains.planning import repository_support as support
from app.domains.task_runtime.xingce_attempt.question_repository import (
    XingceQuestionRepository,
)


BASELINE_COMMIT = "3768145bd0f9faca05dd2ff360336f0c88b00d70"
REPOSITORY_FILES = (
    "repository.py",
    "repository_support.py",
    "capacity_repository.py",
    "candidate_input_repository.py",
    "essay_snapshot_repository.py",
    "plan_storage_repository.py",
    "execution_repository.py",
)
BASELINE_FUNCTION_AST = {
    "__init__": "faed42b631c2d7400d82942c1fe1e051a829bafbb30cc874313abc47752c6e8a",
    "_as_utc": "11cbee4a855aaa0d4b70f4a6f95a075eaf6bc3fad89e6f4b5545f51b8ce83790",
    "_capacity_snapshot": "9ad6068407e1355225fbf665eede9391b286026abac5988774bb907798600f3f",
    "_cause_session_module": "a3bb448b1d224ab7d284c1855e1c374fc40f40827d5970ed417abaceb4bca01d",
    "_essay_closure_context_gap": (
        "00a2dd1da86bc6065d662caa22a829040671d4d979826a01093e692800f2c8dc"
    ),
    "_essay_closure_gap": "0fa1a5d8441d93de20da5f8f3f8981e0cce93f7687a77807df904ce6fd8512ac",
    "_essay_fresh_transfer_content": (
        "755a8388703650d89854fb4c915b3512ed2f9786b8c9a3690d7647bd0b886f8d"
    ),
    "_essay_mastery_snapshot": (
        "a506ac1a4941c139ffb438412e384ac47fa3eba87a2376e0df8c8a3b31f11cf8"
    ),
    "_fingerprint": "e93ed0415568a3e812eaae499e778dd96e80bd4f43c45ff1eb010ac47dc88f9f",
    "_optional_float": "38bdda3a81eff7195f46bb032778dec0dbf167d5e53b09e7a2235d5c6a82f29c",
    "_optional_str": "b50856f74f82b0d80dcddcc1dabc9643aa3ec2f4d42366a8604e7cd612dbbd11",
    "_preference_snapshot": (
        "0ba31608d380eacd6a8da33c7480feab76722e684d6113c01ba7669cf565a231"
    ),
    "_recent_confirmed_cause_direction": (
        "f46f693620e40dc132924019023303a7e8e5676f9652e0aa6e9704121c426228"
    ),
    "_review_is_learning_eligible": (
        "8b3dc3b44322aed93e9261f22786ecc8ea662b0e329cc24120be732d2ff881ce"
    ),
    "_strict_cause_decision_version_id": (
        "ff94afe728f4182319ea0208cd10c2b7d663a8546fa1736547fe89959a5ca105"
    ),
    "_weakest_canonical_module": (
        "9ee4a9e2f3410632e71a1b57d7ae8b8b22a09c42e6e5535e9a7af1554580923a"
    ),
    "_weakest_dimension": (
        "5137e1b3d979d7499cf18928eceabe315dbc9e69b8b3f18ddf8e8c639effe298"
    ),
    "_weakest_dimension_key": (
        "e31350c152d4c7d0a4e0a67eebf1c090ca70d0a71d8d9a49b84511490c1e2ce5"
    ),
    "active_strategy_decisions": (
        "0161404991a9b09893633a97c37b6d7a4a87bd2675b76341635ad9265c306b44"
    ),
    "capacity_by_command": (
        "e51f80fe4d3c92cac4bddda532c0c5801870278dd5b4e3317a7fc0a8a82214a2"
    ),
    "capacity_version": "bade64622e0051f4a97216b6722bd317306e4e33c7896bf5851d99821c05b520",
    "current_capacity": "e8c9e6029b7024babc2dcf53334f5d03291c052dbea9e8fe642432e08d3aef92",
    "current_or_create_capacity": (
        "619880de3afa22e63266a180d0568c4f2c8b68bab8dd21477ddcb58d36105ee9"
    ),
    "current_or_create_preference": (
        "2b83d506b78b20149dcafe2e4d764347a122c0063ee5a20849bcfa2deeb6abe2"
    ),
    "current_plan": "cd8e835df206560dc643dd1edcd061a755b0c066713a4fc0318ce23f50a37e1c",
    "current_plan_for_fingerprint": (
        "e120cd17c57cd921cfa8f25dce81495db034ccc6e494534b150f96f46edfe987"
    ),
    "current_task_execution": (
        "791ca45cfebf346c52535dd44f72f59a3068b02e7db7d4bc8cf61b2bb07d9bb5"
    ),
    "current_task_for_execution": (
        "6a30413c6e01e4410d23e461fdf9f274db5ac11451b242d8fa3322e6d25e95d6"
    ),
    "essay_snapshot": "91b8da3bf09df07d32b526f4031da675bd29dddd62cb1b36f24761507e4e9373",
    "execution_evidence_exists": (
        "09bade9aaae8fc9cfbce3b5ce360c31a330374d046506b31d66e9b5aa93e4df9"
    ),
    "execution_inputs": "692256d9d0273e884208672fb82993b6471bdd766b5d2133ac3f2cfed99be3da",
    "expected_ref": "267c372f6c4d0f5ebd3b65379c873abd7a349d9ad13f3a8160aa85d62c3f6e3e",
    "goal_and_ability": (
        "5ce3c52dd91cca003084c85c0e6e4ff46cc6a0a0648251bb9ca7dcae182d4547"
    ),
    "insert_plan": "6b5d71358323ac8da362921b1356a2f3ed3f924af64382f217cd031c1cef2c6c",
    "insert_plan_task": "9a9225953f6bd6afafbf876c93800296ce807884b8ad2143828ba6050f9033a0",
    "insert_task_execution": (
        "8eb61f34ac141b0a766f5ac03546205e3b4aff1db95aa18bb511cd2a93a2c90e"
    ),
    "insert_user_capacity": (
        "9fa4398f04b562b34d2e51e3a3e8c00dcd89e3dedf87fedc4cdd8550184d5092"
    ),
    "lock_user": "18f5f21500d4ac83225346b5abc15736a14896d1478fc3fa9330fa1c5de40f84",
    "objective_snapshot": (
        "76f3f9d79e3ac33d87a66845efc1bc62581a7f28e4e94005609265a6e13e601b"
    ),
    "owned_task_for_execution": (
        "94c21bd80b4a59e4a5c856ace227e1f79c05e4de69231ce060816d392fb90a81"
    ),
    "plan_for_capacity": (
        "dfc4329436fa50b573588c9b42899b85559db938df6216063cf793ba290edf4f"
    ),
    "plan_for_execution": (
        "a266dae1d3a7c765884ead07db45a27150f7188158a7fab283775e9657b8f4a7"
    ),
    "plan_tasks": "24f2fc28838149fd008adcbb1456c442ea8f0d3102a2ad261434605199351bd3",
    "profile": "58a5bb3ed2271fcee8060fc8e82678ecd048f13594b211ffc637bb62750797c1",
    "recent_track_minutes": (
        "d36921d6acaf4ab010f786325529bcf50ce531aa66cab11b101c64068fff14cf"
    ),
    "task_execution_by_command": (
        "502abef0aaffd4ec1a57a67b87c030a6a17ef58e6c60e27d33f2371bfe165d4b"
    ),
    "weekly_mock_due": "98762722dd7c7924c2518a09b1924adbdf61920f7aac069ac3cc172bcc005053",
}
BASELINE_SNAPSHOT_AST = {
    "ProfileSnapshot": "9e16e1f487c8134774c20c26fcf1f3aa5318dc92ce6fc234b982beb42c4e19fa",
    "PreferenceSnapshot": (
        "0603a586d8a2741df52aed627416f395c77a3228228981c6672d8901e35e01c9"
    ),
    "CapacitySnapshot": "626c4330572192b242611a55ca97d36164676eacffc0cdcc19a7e093a851ebb5",
    "GoalAbilitySnapshot": (
        "e353044916c5bbbd347db589172858d38212d092f4d01eece7962ffbcb1f0b19"
    ),
    "ActiveStrategyDecision": (
        "0658e887240bd89b120b385d419ff478949272aefe9dca9cd1e5070b837adbbe"
    ),
}
BASELINE_SQL_COUNTER = Counter(
    (
        ("joined", "3fc7a116bcd6fe2ee07191d8b4f1c68745397664d2107073c69bd9552e1cb8c9"),
        ("joined", "b0bd27d88d42f07e00c14a6a67ef62f3e488d9953e958b2deb03be5025d97093"),
        ("literal", "09d90e4827e25065d83dc0abe9e37c1cfdf5ace0a409e475cd7f2324325b0ee9"),
        ("literal", "0a7cc8afac4b14cfee508c59110d81d9fea64a82f368ade4f0b63973836600e3"),
        ("literal", "13e4fc2db0f691007843a2444a9274c7ef057b6ce1caaf2a13bcf4a9f7d38570"),
        ("literal", "17f9f629e65b59585e61a780612b6714bbfaf7e69366d4a03e2e52a782deaf1c"),
        ("literal", "1b9d5a1e56b595b7c45559649d12ca863734c9ca7aa79ae39dd4dd0d89c8ca74"),
        ("literal", "286d60518d68a3a8f77c3cf8d38eeaa8e87492d08ea63cce606032206445d2de"),
        ("literal", "3493ee535bf34c6ce9384763244b46dab3d102e7b7cb3695f794dce54af2fecb"),
        ("literal", "380409ba0fc81792383014ddbcd2e7e8bf83147bb34b9f423da68df43e665dd7"),
        ("literal", "3e3589e720914db548f1c3ce52a06ffdb532ba6fd87927a0e85955ad51c9f2a2"),
        ("literal", "44b63a6a4b4aa6eab0bcb0c7dd6a853cd7be210e06967608700bb21d2a7d0afb"),
        ("literal", "4cb03d9eecda0acba73cef787b0530af92ee49e2b9bd853d7972189b6de5b8f0"),
        ("literal", "53595b80ee43b563f81ef4137453cb7dca7831167f9cb932724a9dc47e8c0206"),
        ("literal", "70e956d50e5d6c1bf1421b3d71eef747468cca51848a6fa50438f76491befc40"),
        ("literal", "71566a7f0510dbcfcba22f8cb5a0270810ff6ccc13aca75248e190ba32a18f51"),
        ("literal", "78c443120b9eb2b2980c517e98e50255a46add6ac081701cf45da9f80550c765"),
        ("literal", "7a3d3701118362dfb76e7fc0c999c9fd86b8ad18b9c9d4c218acbf607233a7dc"),
        ("literal", "7b18061462e0e4d4238604406daf499755143e0bc0e06e821a687ad51570f6f8"),
        ("literal", "7e75f220d68636b1c31574261aeb859bc70a1fb56d2cc635f3ff28cf42cfca0a"),
        ("literal", "8f42ce2e7601d1d0b22f0b241f474e4e5877b64cf567239bf8886a7c3f099bfc"),
        ("literal", "915c4dd2c8ee94b797a2cd79af9c89a22848664dc36c390932ec1d612b3137d7"),
        ("literal", "93ef37120fa285309e6e0a4543205ad374272c20c670229d1746a9b9ad799398"),
        ("literal", "98934d97d337c6f8553da6c1fd513a0cc95f19f3bfe5a235c13eb9b935f117f0"),
        ("literal", "999bf964fac9b11719c0941e93366e1e799edeece323e2b4c589b2c3b700598b"),
        ("literal", "9a44f39491873aae7c4719129730b91586def8c11a82a128afabde754f767dc6"),
        ("literal", "9f4323153ff0aec81377479c0c06962193f1dbeac884c0eac30a77f635673e94"),
        ("literal", "a81ef6c6f420d8a71d032bf4ad3c91a2e7558c173eab8550c79f43cf427273fb"),
        ("literal", "a98fb3919116e486132a844c4b6792f54fe8916298f842ce40893096c124286a"),
        ("literal", "aa99ec105f80ce81a0cdb8c1e85d0855ae043b2f0ae361801bb20a619517c3ad"),
        ("literal", "b098e51e9a66f098b8114b416d977d185f84acaf4862f83b6c91aa86e52ccfd7"),
        ("literal", "b11161312c7eb66d2d01b87d540c31a39d1d4fd50163ff5a7bd18a8ed837e88d"),
        ("literal", "b44077b23b7abc2fd1b3fffcc970bb40a42aabbabd13722e41333f44350d41a7"),
        ("literal", "b4a3a27731ad05438163a6c6367da0fa90366286222a889398b76aba028b70bf"),
        ("literal", "be9b4e3f880c94201fd50452ea1f8518ca0f3c4cd0769675811613317cee8416"),
        ("literal", "c4fd2e5533c507682c3a1a442d7ea30f2602bdce5c0de168ddca05bed70ed4f2"),
        ("literal", "c54fb0747dc03819be7b1b1418c1ae2f9b1712274639d00e9cf7d40774cd79b2"),
        ("literal", "cd50de92497cf3c75000c5a08c0069b05ac4a190212f899d8c9b464259e666c8"),
        ("literal", "d317eddb1006cb9ac648bbabec53d2e6fa83d4b824fd251f595d3a5f0c73b387"),
        ("literal", "d4f2f7414ec6fce9dec5a7bfc15a04706b7874badda814fc5273dbfdc58db675"),
        ("literal", "d87b8df2b10cc98ddebb78fb1b0df76f020adf2b37866bbd763ce02f3fb99c70"),
        ("literal", "d9856de64b201019659ecfcd5710863b576b61a63b5207132c93a7e46173a424"),
        ("literal", "da5245c5aace51bf1498fa5558b8bbaa3c04ccd71480484b5d866eb4e2876722"),
        ("literal", "dd47d0c40b0c8e8f4a20510245c0fc9e86441d57a421dfe41706b920f9a3d81b"),
        ("literal", "ec09cff310067436e894b3f807793102ec736d1cf7d069416528498e7cdf23e8"),
        ("literal", "ee6b43f298752add0a25bac1ff0bb832edc533976f44dff2660d5f8f74f1a0a8"),
        ("literal", "f740c76664fb5a404e52eeff05724ebbebaf79ebe12a5fb14b8ac64aa1c6f9cb"),
        ("statement", "393dbf9b132191832e01d7b888e20c201bfd57c49b06a7956fde1a6f5b7f8e17"),
        ("statement", "aa036ccade847382c51b434e79329ecbb14bc32a6a9f0e1078e770fe31339f1b"),
        ("statement", "c746a2f4a556d52b870e408dd6efae1ee1f22f054750fd361d7fa773bb9f8e30"),
        ("statement", "e403caf670ff3272918ae57b298a22ac3204fc4227a50d320700b408e3609938"),
    )
)


def _trees() -> list[ast.Module]:
    planning = Path(__file__).parents[1] / "app" / "domains" / "planning"
    return [ast.parse((planning / name).read_text()) for name in REPOSITORY_FILES]


def _node_hash(node: ast.AST) -> str:
    return sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _function_hashes(trees: list[ast.Module]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assert node.name not in hashes, f"duplicate repository function: {node.name}"
            hashes[node.name] = _node_hash(node)
    return hashes


def _sql_counter(trees: list[ast.Module]) -> Counter[tuple[str, str]]:
    statements: list[tuple[str, str]] = []
    for tree in trees:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "text"
            ):
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    statements.append(
                        ("literal", sha256(argument.value.encode()).hexdigest())
                    )
                elif isinstance(argument, ast.JoinedStr):
                    statements.append(("joined", _node_hash(argument)))
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "statement"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                statements.append(
                    ("statement", sha256(node.value.value.encode()).hexdigest())
                )
    return Counter(statements)


def test_split_preserves_baseline_method_and_sql_ast() -> None:
    trees = _trees()

    assert _function_hashes(trees) == BASELINE_FUNCTION_AST, BASELINE_COMMIT
    snapshot_hashes = {
        node.name: _node_hash(node)
        for tree in trees
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in BASELINE_SNAPSHOT_AST
    }
    assert snapshot_hashes == BASELINE_SNAPSHOT_AST, BASELINE_COMMIT
    sql = _sql_counter(trees)
    assert sum(sql.values()) == 51
    assert sql == BASELINE_SQL_COUNTER, BASELINE_COMMIT


def test_split_preserves_compatibility_exports_and_transaction_ownership() -> None:
    assert not inspect.signature(PlanningRepository).parameters
    repository = PlanningRepository()
    assert isinstance(repository._question_repository, XingceQuestionRepository)
    assert facade.PlanningContractError is PlanningContractError
    for name in (
        "ProfileSnapshot",
        "PreferenceSnapshot",
        "CapacitySnapshot",
        "GoalAbilitySnapshot",
        "ActiveStrategyDecision",
        "_review_is_learning_eligible",
        "_weakest_dimension",
        "_strict_cause_decision_version_id",
    ):
        assert getattr(facade, name) is getattr(support, name)

    forbidden_transaction_calls = {"begin", "begin_nested", "commit", "flush", "rollback"}
    forbidden_domain = "app.domains.xingce_learning"
    for tree in _trees():
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_transaction_calls
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith(forbidden_domain)
            if isinstance(node, ast.Import):
                assert all(not item.name.startswith(forbidden_domain) for item in node.names)
