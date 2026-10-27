// To utdata: én selvstendig IIFE (<script>-tagg, serveres av Python-pakken) og en ESM-modul (npm).
import { build } from "esbuild";

const common = { entryPoints: ["src/index.ts"], bundle: true, minify: true, target: "es2020", legalComments: "none" };
await build({ ...common, format: "iife", outfile: "../server/meldfra/static/meldfra.js" });
await build({ ...common, format: "esm", outfile: "dist/meldfra.esm.js" });
console.log("bygget: server/meldfra/static/meldfra.js + web/dist/meldfra.esm.js");
