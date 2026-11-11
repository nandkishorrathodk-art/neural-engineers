'use client';

import React, { useEffect, useRef } from 'react';
import { BossChatInterface } from './BossChatInterface';

interface BossChatDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  projectId?: string;
  onMissionLaunched?: (projectName: string) => void;
}

export function BossChatDrawer({
  isOpen,
  onClose,
  projectId,
  onMissionLaunched,
}: BossChatDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (isOpen) {
      if (!dialog.open) {
        dialog.showModal();
      }
    } else {
      if (dialog.open) {
        dialog.close();
      }
    }
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialogRef}
      aria-label="Boss AI Executive Chat Drawer"
      {...({ closedby: 'any' } as any)}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className="fixed inset-0 z-50 m-0 p-0 w-full h-full max-w-none max-h-none bg-black/60 backdrop-blur-sm flex justify-end border-0"
      onClick={(e) => {
        if (e.target === dialogRef.current) {
          onClose();
        }
      }}
    >
      <div
        className="w-full max-w-xl h-full bg-[#070d18] border-l border-[#1b2b4a] shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <BossChatInterface
          isDrawer
          onClose={onClose}
          projectId={projectId}
          onMissionLaunched={onMissionLaunched}
          className="h-full rounded-none border-0"
        />
      </div>
    </dialog>
  );
}
