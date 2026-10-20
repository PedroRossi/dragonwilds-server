# shortbow-rapidshot-fix — notes

## The bug

On build `25811348` (UE 5.6.1, CL-248854) the **dedicated server** segfaults when a **player**
ranged projectile lands a hit:

```
SIGSEGV: invalid attempt to read memory at address 0x0000000000000030
UPlayerCombatModeComponent::HandlePlayerEngagedActors(...)         CombatModeArea.h:38   <- faults
  <lambda> ADominionPlayerCharacter::SetupPlayerRangedAttackComponentDelegates(...)::$_6
  TWeakBaseFunctorDelegateInstance<void (FDamageInfo const&), ...>::ExecuteIfSafe
  ADominionProjectile::BroadcastOnDamageApplied(...)
  ADominionProjectile::PerformHit(...)                             DominionProjectile.cpp:653
  ADominionProjectile::Tick(float)                                 DominionProjectile.cpp:445
```

Observed from two crash dumps: in both, the player had an **oak shortbow** (`weap_shortbow_oak`)
equipped ~6 s and ~12 s before, and the server took continuous **enemy** ranged fire without
crashing (enemy projectiles use `DominionAiRangedAttackComponent`, a different path). A longbow
fired fine. This points at the shortbow's **Rapidshot** special attack.

It's a null-pointer read in compiled C++ — only Jagex can truly fix it. This mod is a
**content-layer workaround**: change the shortbow data so it stops exercising the crashing path.

## Assets (found in the plaintext container index)

| Asset | Role |
| --- | --- |
| `PA_RangedAttack_Shortbow_Rapidshot` | the Rapidshot special — **shared by all shortbows** |
| `BP_PlayerRangedAttackCollection_Shortbow` | which ranged attacks a shortbow has |
| `PA_RangedAttack_Shortbow`, `..._QuickAction` | the normal / quick shortbow attacks |
| `BP_Components_PlayerRangedAttack` | the `UPlayerRangedAttackComponent` from the stack |
| `BP_Components_PlayerCombatMode` | the `UPlayerCombatModeComponent` that faults |
| `ITEM_Shortbow_Oak`, `BP_Shortbow_Oak` | the oak shortbow item/weapon |

## The edit (do this with the `.usmap` + UAssetGUI)

`build.sh extract` pulls these to `work/legacy/`. Open the target in UAssetGUI with the Dragonwilds
`Mappings.usmap`, then apply the smallest change that stops the crash — try in this order and test
each on a throwaway world:

1. **`PA_RangedAttack_Shortbow_Rapidshot`** — disable the special, or cut its multi-projectile
   burst down to a single shot (the rapid multi-hit is the suspected trigger).
2. **`BP_PlayerRangedAttackCollection_Shortbow`** — remove the Rapidshot entry so shortbows fall
   back to their safe Snapshot attack.

Then trim `work/legacy/` to just the edited asset(s) (keep their folder paths) and run
`build.sh pack`. Deploy with `uv run dw mod install dist` → `uv run dw restart`.

## Verify it worked

On a test world, fire a shortbow (ideally its Rapidshot) at something until hits land. Before the
fix the server segfaults (`dw status` shows `CRASHED`, and `~/dragonwilds-backups` gets a
`saves-*-crash.tgz`); after, it survives. Keep the Jagex bug report open regardless — this only
stops *our* server crashing, by changing how the shortbow plays.
