// Bloc de marque des écrans d'entrée (connexion, inscription, mot de passe
// oublié/réinitialisé, contact). C'est la première chose que voit un partenaire :
// le logo et le nom doivent être lisibles d'emblée, pas discrets.
//
// Centralisé ici car les 5 pages en portaient une copie indépendante du même
// markup — elles divergeaient au moindre ajustement.
export default function AuthBrand({ subtitle = 'Plateforme Partenaires' }) {
  return (
    <div className="flex items-center gap-3.5 mb-8">
      <img src="/logo.png" alt="Groupement-IT" className="h-14 w-14 object-contain shrink-0" />
      <div className="leading-tight">
        <div className="text-[22px] font-semibold tracking-tightest text-[var(--text)]">Groupement-IT</div>
        <div className="text-[13px] mt-0.5 text-[var(--text-faint)]">{subtitle}</div>
      </div>
    </div>
  )
}
