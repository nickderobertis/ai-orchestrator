import nx from "@nx/eslint-plugin";
import tseslint from "typescript-eslint";

export default tseslint.config(
  ...tseslint.configs.recommended,
  {
    ignores: ["**/dist/**", "**/node_modules/**"],
  },
  {
    files: ["**/*.ts", "**/*.tsx"],
    plugins: { "@nx": nx },
    rules: {
      "@nx/enforce-module-boundaries": [
        "error",
        {
          allow: [],
          depConstraints: [
            {
              sourceTag: "type:app",
              onlyDependOnLibsWithTags: ["type:feature", "type:data-access", "type:ui", "type:util"],
            },
            {
              sourceTag: "type:feature",
              onlyDependOnLibsWithTags: ["type:data-access", "type:ui", "type:util"],
            },
            {
              sourceTag: "type:data-access",
              onlyDependOnLibsWithTags: ["type:util"],
            },
            {
              sourceTag: "type:ui",
              onlyDependOnLibsWithTags: ["type:ui", "type:util"],
            },
            {
              sourceTag: "type:util",
              onlyDependOnLibsWithTags: ["type:util"],
            }
          ],
          enforceBuildableLibDependency: true
        }
      ]
    }
  }
);
