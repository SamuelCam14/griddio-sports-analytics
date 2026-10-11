interface TeamLogoProps {
  name: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  className?: string
  sport?: 'NBA' | 'NFL' | string
}

const TEAM_PALETTES: Record<string, { bg: string; text: string; abbr: string }> = {
  // NBA
  'Boston Celtics': { bg: 'from-emerald-900/60 to-slate-900', text: 'text-emerald-200', abbr: 'BOS' },
  'Los Angeles Lakers': { bg: 'from-purple-950/70 to-amber-950/40', text: 'text-amber-200', abbr: 'LAL' },
  'Golden State Warriors': { bg: 'from-blue-950/70 to-slate-900', text: 'text-blue-200', abbr: 'GSW' },
  'Denver Nuggets': { bg: 'from-sky-950/70 to-amber-950/40', text: 'text-sky-200', abbr: 'DEN' },
  'Milwaukee Bucks': { bg: 'from-emerald-950/70 to-slate-900', text: 'text-emerald-200', abbr: 'MIL' },
  'Miami Heat': { bg: 'from-rose-950/70 to-slate-900', text: 'text-rose-200', abbr: 'MIA' },
  'Phoenix Suns': { bg: 'from-purple-950/70 to-orange-950/40', text: 'text-orange-200', abbr: 'PHX' },
  'Dallas Mavericks': { bg: 'from-blue-950/70 to-slate-900', text: 'text-blue-200', abbr: 'DAL' },
  'Philadelphia 76ers': { bg: 'from-blue-950/70 to-rose-950/40', text: 'text-blue-200', abbr: 'PHI' },
  'New York Knicks': { bg: 'from-blue-950/70 to-orange-950/40', text: 'text-orange-200', abbr: 'NYK' },
  'Oklahoma City Thunder': { bg: 'from-sky-950/70 to-orange-950/40', text: 'text-sky-200', abbr: 'OKC' },
  'Minnesota Timberwolves': { bg: 'from-blue-950/70 to-slate-900', text: 'text-blue-200', abbr: 'MIN' },
  'Cleveland Cavaliers': { bg: 'from-red-950/70 to-slate-900', text: 'text-red-200', abbr: 'CLE' },
  'LA Clippers': { bg: 'from-blue-950/70 to-red-950/40', text: 'text-blue-200', abbr: 'LAC' },
  'Memphis Grizzlies': { bg: 'from-sky-950/70 to-slate-900', text: 'text-sky-200', abbr: 'MEM' },
  'Sacramento Kings': { bg: 'from-purple-950/70 to-slate-900', text: 'text-purple-200', abbr: 'SAC' },
  // NFL
  'Kansas City Chiefs': { bg: 'from-red-950/70 to-slate-900', text: 'text-red-200', abbr: 'KC' },
  'San Francisco 49ers': { bg: 'from-red-950/70 to-amber-950/40', text: 'text-amber-200', abbr: 'SF' },
  'Philadelphia Eagles': { bg: 'from-emerald-950/70 to-slate-900', text: 'text-emerald-200', abbr: 'PHI' },
  'Baltimore Ravens': { bg: 'from-purple-950/70 to-slate-900', text: 'text-purple-200', abbr: 'BAL' },
  'Buffalo Bills': { bg: 'from-blue-950/70 to-red-950/40', text: 'text-blue-200', abbr: 'BUF' },
  'Dallas Cowboys': { bg: 'from-slate-900 to-blue-950/70', text: 'text-blue-200', abbr: 'DAL' },
  'Detroit Lions': { bg: 'from-sky-950/70 to-slate-900', text: 'text-sky-200', abbr: 'DET' },
  'Miami Dolphins': { bg: 'from-teal-950/70 to-orange-950/40', text: 'text-teal-200', abbr: 'MIA' },
}

export function TeamLogo({ name, size = 'md', className = '' }: TeamLogoProps) {
  const palette = TEAM_PALETTES[name] ?? {
    bg: 'from-slate-800 to-slate-900',
    text: 'text-slate-200',
    abbr: name.split(' ').map((n) => n[0]).join('').slice(0, 3).toUpperCase(),
  }

  const sizeClasses = {
    sm: 'h-6 w-6 text-[10px] rounded-md',
    md: 'h-8 w-8 text-xs rounded-lg',
    lg: 'h-11 w-11 text-sm rounded-xl',
    xl: 'h-16 w-16 text-base font-bold sm:h-18 sm:w-18 rounded-2xl',
  }

  return (
    <div
      title={name}
      className={`inline-grid shrink-0 place-items-center bg-gradient-to-b border border-white/[0.1] font-mono font-bold tracking-wider select-none ${palette.bg} ${palette.text} ${sizeClasses[size]} ${className}`}
    >
      <span>{palette.abbr}</span>
    </div>
  )
}
