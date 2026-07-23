import coreWebVitals from "eslint-config-next/core-web-vitals"

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "out/**", "build/**", "dist/**"],
  },
  ...coreWebVitals,
]

export default eslintConfig
