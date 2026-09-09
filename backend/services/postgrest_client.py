"""
Client de la base de données — un client **PostgREST**, pas « un client Supabase ».

POURQUOI CE FICHIER NE S'APPELLE PLUS supabase_client.py

Il s'appelait ainsi parce qu'il parlait à Supabase. Mais ce qu'il construit n'a
jamais été spécifique à Supabase : c'est un client de l'API **PostgREST**, que
Supabase expose sous `/rest/v1` et que le VPS expose au même chemin derrière une
façade nginx (`deploy/nginx-postgrest.conf`). Le même objet, le même protocole,
les mêmes appels — seule l'adresse change, et elle tient dans une variable
d'environnement.

Le nom d'hébergeur faisait croire à une dépendance là où il n'y a qu'une URL.
C'est cette confusion qui a coûté le plus cher : elle a fait jouer une migration
sur la base du VPS alors que la production lisait Supabase, et le lien de
désabonnement échouait pendant que la table existait — dans l'autre base.

CE QUE LE NOM NE DIT TOUJOURS PAS, ET C'EST VOULU

Il ne dit pas OÙ est la base. C'est `DATABASE_URL` (ou son ancien nom
`SUPABASE_URL`, toujours accepté) qui le dit, et elle seule. Pour le savoir :

    grep -E '^(DATABASE_URL|SUPABASE_URL)=' backend/.env

La bibliothèque `supabase-py` est conservée sciemment : elle est ici un simple
client HTTP PostgREST, et c'est ce choix qui réduit un changement de base à une
ligne de configuration plutôt qu'à une réécriture de 42 fichiers.
"""
from supabase import Client, create_client

from config import settings

#: Le client partagé. Construit à l'import : toute la base est derrière lui.
db: Client = create_client(settings.supabase_url, settings.supabase_service_key)
