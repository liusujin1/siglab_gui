  function vihelp(arg)
% function vihelp(arg)
% Function to locate and invoke help system.  
% Typing vihelp at MATLAB command window prompt brings up the SigLab Help Center.
% Dick Benson, DSP Technology
   [drv,pn]=pathfind('siglab\doc');
   eval(['siglab(''exe'','''[drv,pn,'\helpcent.pdf'')']]);
% end function



