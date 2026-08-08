# Agent Skills

The executable skill surface is intentionally empty right now.

The Agent Loop and registry still support skills, but the previous built-in UAV
workflow skills were removed because they were too rule-driven and brittle.

Current default behavior:

- The LLM receives backend-native atomic/provider tools.
- `SKILL.md` files are loaded as LLM guidance, not as callable tools.
- `flight_sequence/SKILL.md` teaches the Agent how to handle short ordered UAV
  sequences such as status -> takeoff -> move -> photo/VLM -> return -> land.
- Legacy Python skills such as navigation/search/return_home are not registered
  unless migration tests opt in explicitly.
- A future executable domain skill should still start as a Markdown contract;
  optional code belongs under the skill folder as implementation hints or helper
  scripts, not as the primary policy path.
