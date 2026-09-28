import * as Tooltip from '@radix-ui/react-tooltip'
import type { ReactElement, ReactNode } from 'react'

// Accessible hover/focus tooltip (Radix). Carries its own provider so any
// component can use it standalone, including in server-rendered tests.
export default function Tip({
  content,
  children,
  side = 'top',
}: {
  content: ReactNode
  children: ReactElement
  side?: 'top' | 'bottom' | 'left' | 'right'
}) {
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className="tip" side={side} sideOffset={6} collisionPadding={10}>
            {content}
            <Tooltip.Arrow className="tip-arrow" width={10} height={5} />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
