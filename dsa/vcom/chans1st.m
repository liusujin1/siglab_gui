function [inchans,outchans,bw,id]=chans1st(In1);
% function [inchans,outchans,bw,id]=chans1st(In1);

% gls, dspt

[Nin Nout bw  junk ] = siglab('ioinit',' notafile.xxx',0);
 inchans = 0;
 outchans = 0;
 id = -1;

   for id = 0:6
      [sn inchans outchans] = siglab('debug',-20,id);
      if inchans > 0
        break;
      end;
   end;
 
