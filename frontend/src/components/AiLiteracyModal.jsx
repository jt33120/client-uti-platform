import { useState } from 'react'
import { X, Loader2, GraduationCap, Check, AlertTriangle, Eye, Scale } from 'lucide-react'
import api from '../lib/api'

// Module de sensibilisation à l'IA — AI Act art. 4 (littératie).
//
// C'est la SEULE obligation de l'AI Act déjà exigible pour nous : elle n'est pas
// concernée par le report au 2 décembre 2027. Obligation de MOYENS — ce qui se
// démontre en contrôle, c'est la trace (qui, quoi, quand), d'où l'attestation
// horodatée et versionnée côté serveur (services/ai_literacy.py).
//
// Le contenu vit ici et non en base : c'est du texte destiné à être lu, pas de la
// donnée. Toute évolution de FOND doit s'accompagner d'un bump de
// `ai_literacy.VERSION` côté backend, qui repasse tout le monde en « à refaire ».

const SECTIONS = [
  {
    Icon: Eye,
    title: 'Ce que le score mesure — et ce qu’il ne mesure pas',
    body: (
      <>
        Le score sur 100 est une <strong>aide au tri</strong>, pas un jugement de valeur sur
        une personne. Il combine une grille déterministe (des règles écrites, versionnées,
        que vous réglez vous-même en étoiles) et un second avis produit par un modèle de
        langage. Quand les deux divergent fortement, le système <strong>retombe
        automatiquement sur la grille</strong> — le modèle ne peut pas emporter la décision à
        lui seul.
        <br /><br />
        Un critère à 0 étoile est <strong>totalement exclu</strong> : la donnée n’est pas
        transmise au modèle et n’apparaît nulle part dans la notation.
      </>
    ),
  },
  {
    Icon: AlertTriangle,
    title: 'Les limites que vous devez connaître',
    body: (
      <>
        Le modèle lit un CV, pas une carrière. Il ne sait pas si la mission s’est bien
        passée, si la personne est disponible réellement, ni si elle s’entendra avec
        l’équipe cliente. Il peut <strong>surévaluer un CV bien rédigé</strong> et
        sous-évaluer un profil solide mais mal présenté — c’est un biais connu et il joue
        contre les candidats les moins à l’aise à l’écrit.
        <br /><br />
        Il peut aussi se tromper franchement : confondre deux technologies proches,
        mal dater une expérience. C’est pour cela que chaque affirmation de l’IA est
        reliée au passage exact du CV source, surligné : <strong>vérifiez-le</strong> avant
        de vous en servir comme argument auprès d’un client.
      </>
    ),
  },
  {
    Icon: Scale,
    title: 'Le biais d’automatisation — le vrai risque',
    body: (
      <>
        Le risque principal n’est pas que l’IA se trompe : c’est qu’<strong>on la suive
        sans regarder</strong>. On appelle ça le biais d’automatisation, et il augmente avec
        la fatigue et le volume.
        <br /><br />
        Concrètement : valider une liste préclassée sans ouvrir les profils du bas
        revient, en droit, à laisser la machine décider seule — même si un humain a
        cliqué. La CNIL considère qu’une décision devient « exclusivement automatisée »
        <strong> par construction</strong> lorsque les candidatures reléguées ne sont pas
        réellement examinées, faute de temps.
        <br /><br />
        Prenez l’habitude d’ouvrir au moins un profil situé hors du top 3. Si vous
        n’êtes pas d’accord avec le classement, <strong>passez outre</strong> : la
        justification qui vous est demandée n’est pas une formalité, c’est la preuve que
        la décision est la vôtre.
      </>
    ),
  },
  {
    Icon: GraduationCap,
    title: 'Votre responsabilité',
    body: (
      <>
        La décision de présenter ou d’écarter un consultant est <strong>toujours la
        vôtre</strong>, jamais celle du système. Le classement propose, vous disposez.
        <br /><br />
        Les contenus rédigés par l’IA (résumé d’appel d’offres, synthèse du vivier, CV
        reformaté, motif de refus) portent une mention le signalant. Relisez-les avant
        toute diffusion externe : dès lors que vous les envoyez, <strong>vous en assumez le
        contenu</strong>.
      </>
    ),
  },
]

export default function AiLiteracyModal({ onClose, onAcknowledged }) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const acknowledge = async () => {
    setSaving(true); setError('')
    try {
      // Aucun corps de requête : c'est le serveur qui décide de quelle version on
      // atteste — sinon on pourrait attester d'un contenu jamais affiché.
      const { data } = await api.post('/auth/me/ai-literacy')
      onAcknowledged?.(data)
      onClose?.()
    } catch (e) {
      setError(e.response?.data?.detail || 'Enregistrement impossible. Réessayez.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="card p-0 w-full max-w-2xl max-h-[92vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 p-4 border-b" style={{ borderColor: 'var(--border)' }}>
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <GraduationCap size={15} className="text-brand-400" />
            Bien utiliser l’aide au tri par IA
          </h2>
          <button onClick={onClose} className="btn-ghost p-1.5" aria-label="Fermer"><X size={14} /></button>
        </div>

        <div className="overflow-y-auto p-5 space-y-5">
          <p className="text-[12.5px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            Cinq minutes de lecture. Ce module est requis par l’article 4 du règlement
            européen sur l’IA, qui impose que les personnes opérant un système d’IA en
            comprennent le fonctionnement et les limites. Votre lecture est enregistrée et
            renouvelée chaque année.
          </p>

          {SECTIONS.map(({ Icon, title, body }, i) => (
            <div key={i} className="flex gap-3">
              <div className="w-7 h-7 rounded-md flex items-center justify-center shrink-0 mt-0.5"
                   style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
                <Icon size={14} />
              </div>
              <div className="min-w-0">
                <p className="text-[13px] font-semibold mb-1" style={{ color: 'var(--text)' }}>{title}</p>
                <p className="text-[12.5px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>{body}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="border-t p-4 flex items-center justify-between gap-3 flex-wrap"
             style={{ borderColor: 'var(--border)' }}>
          {error
            ? <p className="text-[12px]" style={{ color: 'var(--danger)' }}>{error}</p>
            : <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
                Votre attestation est horodatée et conservée dans le registre de conformité.
              </p>}
          <button onClick={acknowledge} disabled={saving}
                  className="btn-primary text-xs px-4 py-2 inline-flex items-center gap-1.5 ml-auto">
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            J’ai lu et compris
          </button>
        </div>
      </div>
    </div>
  )
}
