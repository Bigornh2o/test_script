"""
Extraction de la structure de la fenêtre « Portfolio Statement Parameters » de Cockpit.

Usage :
    python extraire_modale_cockpit.py

1. Chrome s'ouvre sur Cockpit : connecte-toi normalement.
2. Sélectionne un portefeuille et ouvre Statements > Portfolio
   (la fenêtre « Portfolio Statement Parameters » doit être affichée).
3. Reviens dans cette console et appuie sur Entrée.

Le script ne modifie rien dans Cockpit (il ouvre juste les listes déroulantes
pour lire leurs options, puis les referme) et ne clique jamais sur Generate.

Fichiers produits à côté du script :
    cockpit_modale_champs.txt   -> résumé lisible, à envoyer en priorité
    cockpit_modale_champs.json  -> même contenu, en détail
    cockpit_modale_form.html    -> HTML du formulaire, nettoyé

Données client : seul le bloc des paramètres est extrait (pas le panneau du
portefeuille). Les textes libres sont remplacés par leur longueur, et toute
suite de 5 chiffres ou plus est masquée. Relis quand même les fichiers avant
de les envoyer à l'extérieur, selon les règles internes.
"""

import json
import os
import re
import sys

from playwright.sync_api import sync_playwright

URL_LOGIN = "https://cockpitind.ch.pbk/#/login"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TITRE_MODALE = "Portfolio Statement Parameters"

DOSSIER_SORTIE = os.path.dirname(os.path.abspath(sys.argv[0])) or "."

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ------------------------------------------------------------------
# JavaScript exécuté dans la page
# ------------------------------------------------------------------
JS_INVENTAIRE = r"""
(titreModale) => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const texte = (el) => (el ? (el.innerText || el.textContent || "") : "").replace(/\s+/g, " ").trim();
  const masquer = (t) => String(t || "").replace(/\d{5,}/g, (m) => "#".repeat(m.length));

  // 1. Racine du formulaire = plus petit ancêtre commun de tous les [formcontrolname] visibles
  const controles = [...document.querySelectorAll("[formcontrolname]")].filter(visible);
  if (!controles.length) return { erreur: "Aucun champ [formcontrolname] visible : la fenêtre est-elle ouverte ?" };

  const ancetres = (el) => { const l = []; while (el) { l.push(el); el = el.parentElement; } return l; };
  let racine = controles[0];
  const chaine0 = ancetres(controles[0]);
  for (const cand of chaine0) {
    if (controles.every((c) => cand.contains(c))) { racine = cand; break; }
  }

  // 2. Libellé d'un champ
  const libelle = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return texte(l);
    }
    const lab = el.closest("label");
    if (lab) return texte(lab);
    let a = el.parentElement;
    for (let i = 0; i < 4 && a && a !== racine; i++, a = a.parentElement) {
      const cands = [...a.querySelectorAll("label, .label, [class*='label'], span, p")]
        .filter((x) => !x.contains(el) && !el.contains(x) && visible(x) && texte(x).length > 0 && texte(x).length < 80
                       && !x.closest("ng-select, button, .ng-dropdown-panel"));
      // le libellé qui précède le champ dans le document
      const avant = cands.filter((x) => x.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (avant.length) return texte(avant[avant.length - 1]);
      if (cands.length) return texte(cands[0]);
    }
    return "";
  };

  // 3. Parcours dans l'ordre du document : titres de section + champs + boutons
  const elements = [racine, ...racine.querySelectorAll("*")];
  const resultat = [];
  let section = "";
  const deja = new Set();

  const estTitre = (el) => {
    if (!visible(el) || el.closest("button, label, ng-select, .ng-dropdown-panel")) return false;
    if (/^H[1-6]$|^LEGEND$/.test(el.tagName)) return true;
    if (el.childElementCount !== 0) return false;
    const t = texte(el);
    return t.length >= 4 && t.length <= 60 && /[A-Z]/.test(t) && t === t.toUpperCase() && !/^[\d\s.,/-]+$/.test(t);
  };

  for (const el of elements) {
    if (estTitre(el)) {
      section = texte(el);
      resultat.push({ genre: "section", titre: section });
      continue;
    }
    if (!visible(el)) continue;

    const fcn = el.getAttribute("formcontrolname");
    const tag = el.tagName.toLowerCase();
    const estCase = tag === "input" && el.type === "checkbox";

    if (fcn || estCase || tag === "textarea" || (tag === "input" && !el.closest("[formcontrolname]"))) {
      if (deja.has(el)) continue;
      deja.add(el);
      const champ = {
        genre: "champ",
        section,
        libelle: masquer(libelle(el)),
        formcontrolname: fcn || (el.closest("[formcontrolname]") ? el.closest("[formcontrolname]").getAttribute("formcontrolname") : ""),
        balise: tag,
        type: tag === "input" ? el.type : "",
        id: el.id || "",
        obligatoire: el.hasAttribute("required") || /\*/.test(libelle(el)),
        classes: (el.className && typeof el.className === "string") ? el.className.split(/\s+/).filter((c) => c && !c.startsWith("ng-tns")).slice(0, 8).join(" ") : "",
      };
      if (estCase) {
        champ.coche = el.checked;
      } else if (tag === "ng-select" || el.querySelector(".ng-value, .ng-select-container")) {
        champ.liste_deroulante = true;
        champ.valeur = masquer(texte(el.querySelector(".ng-value-label, .ng-value")));
        champ.croix_pour_vider = !!el.querySelector(".ng-clear-wrapper");
      } else if (tag === "textarea" || (tag === "input" && /text|search|email|tel|url|number/.test(el.type))) {
        const v = el.value || "";
        champ.valeur = (fcn === "valuationDate") ? v : `<${v.length} caractères>`;
        if (el.maxLength > 0) champ.longueur_max = el.maxLength;
      } else if (tag === "input") {
        champ.valeur = el.type === "radio" ? (el.checked ? "sélectionné" : "") : "";
      }
      resultat.push(champ);
      continue;
    }

    if (tag === "button" && !el.closest("ng-select")) {
      const t = texte(el);
      if (!t) continue;
      resultat.push({
        genre: "bouton",
        section,
        texte: masquer(t),
        actif: /active|selected|primary|btn-dark/.test(el.className) || el.getAttribute("aria-pressed") === "true",
        classes: (typeof el.className === "string") ? el.className.split(/\s+/).filter((c) => c && !c.startsWith("ng-tns")).slice(0, 8).join(" ") : "",
      });
    }
  }

  // 4. HTML nettoyé du formulaire
  const copie = racine.cloneNode(true);
  copie.querySelectorAll("script, style, svg, img").forEach((n) => n.remove());
  copie.querySelectorAll("*").forEach((n) => {
    [...n.attributes].forEach((a) => {
      if (/^_ng|^ng-reflect|^style$/.test(a.name)) n.removeAttribute(a.name);
      if (a.name === "value" && !(n.type === "checkbox" || n.type === "radio")) n.setAttribute("value", "");
    });
    if (n.tagName === "TEXTAREA") n.textContent = "";
  });
  const marcheur = document.createTreeWalker(copie, NodeFilter.SHOW_TEXT);
  let noeud;
  while ((noeud = marcheur.nextNode())) noeud.nodeValue = masquer(noeud.nodeValue);

  return { champs: resultat, html: copie.outerHTML.replace(/\n\s*\n+/g, "\n") };
}
"""


def lire_options_listes(page, champs):
    """Ouvre chaque liste déroulante, lit ses options puis la referme (sans rien sélectionner)."""
    for champ in champs:
        if champ.get("genre") != "champ" or not champ.get("liste_deroulante") or not champ.get("formcontrolname"):
            continue
        nom = champ["formcontrolname"]
        try:
            liste = page.locator(f'[formcontrolname="{nom}"]').first
            liste.scroll_into_view_if_needed()
            liste.locator(".ng-select-container, .ng-arrow-wrapper").first.click()
            page.wait_for_timeout(600)
            options = page.locator(".ng-dropdown-panel .ng-option").all_inner_texts()
            champ["options"] = [re.sub(r"\d{5,}", lambda m: "#" * len(m.group()), o.strip()) for o in options[:80]]
            if len(options) > 80:
                champ["options"].append(f"... ({len(options)} options au total)")
        except Exception as e:
            champ["options"] = f"lecture impossible : {e}"
        finally:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass


def ecrire_resume(champs, chemin):
    lignes = [f"Structure de la fenêtre « {TITRE_MODALE} »", ""]
    for c in champs:
        if c["genre"] == "section":
            lignes += ["", f"=== {c['titre']} ==="]
        elif c["genre"] == "bouton":
            lignes.append(f"  [bouton] {c['texte']}{'  (actif)' if c['actif'] else ''}   classes: {c['classes']}")
        else:
            etoile = " *" if c.get("obligatoire") and "*" not in (c.get("libelle") or "") else ""
            details = [f"formcontrolname={c['formcontrolname'] or '-'}", f"{c['balise']}{('/' + c['type']) if c['type'] else ''}"]
            if "coche" in c:
                details.append("coché" if c["coche"] else "non coché")
            if c.get("liste_deroulante"):
                details.append(f"valeur='{c.get('valeur', '')}'")
                details.append("croix ×" if c.get("croix_pour_vider") else "pas de croix")
            elif "valeur" in c:
                details.append(f"valeur={c['valeur']}")
            if c.get("longueur_max"):
                details.append(f"max {c['longueur_max']}")
            lignes.append(f"  [champ] {c['libelle'] or '(sans libellé)'}{etoile}   " + " | ".join(details))
            if isinstance(c.get("options"), list):
                lignes.append(f"          options : {', '.join(o if o else '(vide)' for o in c['options'])}")
            elif c.get("options"):
                lignes.append(f"          options : {c['options']}")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")


def main():
    with sync_playwright() as p:
        navigateur = p.chromium.launch(executable_path=CHROME_PATH, headless=False)
        page = navigateur.new_context().new_page()
        page.set_viewport_size({"width": 1650, "height": 1000})
        page.goto(URL_LOGIN, wait_until="domcontentloaded")

        print("\n1. Connecte-toi à Cockpit dans la fenêtre Chrome.")
        print(f"2. Ouvre la fenêtre « {TITRE_MODALE} » pour un portefeuille.")
        input("3. Puis appuie sur Entrée ici... ")

        try:
            page.wait_for_selector(f"text={TITRE_MODALE}", state="visible", timeout=5000)
        except Exception:
            print(f"\nATTENTION : le titre « {TITRE_MODALE} » n'est pas visible, extraction tentée quand même.")

        resultat = page.evaluate(JS_INVENTAIRE, TITRE_MODALE)
        if resultat.get("erreur"):
            print("\nERREUR :", resultat["erreur"])
            input("Appuie sur Entrée pour fermer...")
            navigateur.close()
            return

        champs = resultat["champs"]
        print(f"\n{sum(1 for c in champs if c['genre'] == 'champ')} champ(s) trouvé(s), lecture des listes déroulantes...")
        lire_options_listes(page, champs)

        chemin_txt = os.path.join(DOSSIER_SORTIE, "cockpit_modale_champs.txt")
        chemin_json = os.path.join(DOSSIER_SORTIE, "cockpit_modale_champs.json")
        chemin_html = os.path.join(DOSSIER_SORTIE, "cockpit_modale_form.html")

        ecrire_resume(champs, chemin_txt)
        with open(chemin_json, "w", encoding="utf-8") as f:
            json.dump(champs, f, indent=2, ensure_ascii=False)
        with open(chemin_html, "w", encoding="utf-8") as f:
            f.write(resultat["html"])

        print("\nFichiers créés :")
        for chemin in (chemin_txt, chemin_json, chemin_html):
            print(f"  {chemin}  ({os.path.getsize(chemin) // 1024 + 1} Ko)")
        print("\nEnvoie-moi en priorité le .txt (et le .json si possible).")

        input("\nAppuie sur Entrée pour fermer Chrome...")
        navigateur.close()


if __name__ == "__main__":
    main()
