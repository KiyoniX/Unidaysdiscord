# UNiDAYS → Discord

Bot qui poste automatiquement les nouvelles offres étudiantes [UNiDAYS](https://www.myunidays.com/FR/fr-FR) (France) dans un salon Discord.

Toutes les 30 minutes, il lit les offres publiques de la page d'accueil d'UNiDAYS et poste celles qu'il n'a jamais vues : marque, réduction, logo, date de fin et lien vers l'offre. Chaque étudiant génère ensuite son propre code sur UNiDAYS avec son compte. Le bot ne touche jamais aux codes ni aux comptes.

Il tourne gratuitement sur GitHub Actions : pas de serveur ni de PC allumé en permanence.

---

## Installer le bot sur ton serveur Discord (fork)

Compte une dizaine de minutes. Il te faut un compte GitHub et les droits « Gérer les webhooks » sur ton serveur Discord.

### 1. Créer le webhook Discord

1. Sur Discord, fais un clic droit sur le salon où poster les offres, puis **Modifier le salon**.
2. Va dans **Intégrations → Webhooks → Nouveau webhook**.
3. Donne-lui un nom (par exemple `UNiDAYS`), puis clique sur **Copier l'URL du webhook**.

> ⚠️ Garde cette URL secrète : n'importe qui la connaissant peut poster dans ton salon. Ne la mets jamais dans le code.

### 2. Forker le repo

1. En haut à droite de cette page, clique sur **Fork**.
2. Laisse les options par défaut et clique sur **Create fork**.

Tu as maintenant ta propre copie du bot, sur `github.com/TON_PSEUDO/Unidaysdiscord`.

### 3. Ajouter le webhook en secret

Dans **ton fork** :

1. Va dans **Settings → Secrets and variables → Actions**.
2. Clique sur **New repository secret**.
3. Dans **Name**, mets `DISCORD_WEBHOOK_URL`, exactement ce nom.
4. Dans **Secret**, colle l'URL du webhook.
5. Clique sur **Add secret**.

Pour poster sur plusieurs salons ou serveurs, mets plusieurs URLs dans ce même secret, **une par ligne**.

### 4. Autoriser le bot à enregistrer les offres vues

Le bot retient les offres déjà postées dans `seen_offers.json`, qu'il met à jour lui-même dans le repo.

1. Va dans **Settings → Actions → General**.
2. Tout en bas, dans **Workflow permissions**, coche **Read and write permissions**.
3. Clique sur **Save**.

Sans ça, le bot échoue à la fin de chaque passage et reposte les mêmes offres.

### 5. Activer les Actions

Par sécurité, GitHub désactive les Actions sur les forks.

1. Va dans l'onglet **Actions** de ton fork.
2. Clique sur **I understand my workflows, go ahead and enable them**.
3. Dans la colonne de gauche, clique sur **UNiDAYS -> Discord**. Si un bandeau indique que les workflows programmés sont désactivés, clique sur **Enable workflow**.

### 6. Tester

1. Toujours dans **Actions → UNiDAYS -> Discord**, clique sur **Run workflow** puis sur le bouton vert **Run workflow**.
2. Attends environ 30 secondes : la ligne doit passer au vert ✅.

Le fork récupère la liste `seen_offers.json` du repo d'origine. Le premier passage ne reposte donc pas toutes les offres existantes, seulement celles apparues depuis. Ensuite le bot tourne tout seul toutes les 30 minutes.

> Pour voir tout de suite le rendu dans ton salon, vide le fichier `seen_offers.json` de ton fork (remplace son contenu par `[]`) avant de lancer le workflow. Le bot postera alors les offres actuelles, 10 au maximum par passage.

---

## Dépannage

| Problème | Solution |
|---|---|
| ❌ `DISCORD_WEBHOOK_URL n'est pas défini` | Le secret manque ou est mal nommé (étape 3). |
| ❌ Erreur au moment du `git push` | Les permissions ne sont pas en lecture et écriture (étape 4). |
| ⚠️ `Webhook n°X en erreur (404)` | Le webhook a été supprimé sur Discord : recrée-le et mets à jour le secret. |
| ❌ `Aucune offre trouvée` | UNiDAYS a changé la structure de son site : ouvre une issue sur le repo d'origine. |
| Le bot ne tourne plus tout seul | GitHub met en pause les workflows programmés après 60 jours sans activité. Réactive-le dans l'onglet Actions. |
| Les passages sont en retard | C'est normal : GitHub peut décaler les tâches programmées de plusieurs minutes. |

## Récupérer les mises à jour

Quand le repo d'origine évolue, va sur la page de ton fork et clique sur **Sync fork → Update branch**.

Si GitHub signale un conflit (sur `seen_offers.json`, puisque chaque bot met à jour le sien) et propose seulement **Discard commits**, tu peux accepter. Ton fork reprendra la liste du repo d'origine : au pire, une ou deux offres seront manquées ou repostées.

## Lancer en local

```powershell
pip install -r requirements.txt
python unidays_bot.py --dry-run          # affiche les nouvelles offres sans rien poster
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python unidays_bot.py                    # poste les nouvelles offres
```

---

Projet étudiant, non affilié à UNiDAYS. Le bot ne relaie que des informations publiques et renvoie toujours vers le site officiel.
