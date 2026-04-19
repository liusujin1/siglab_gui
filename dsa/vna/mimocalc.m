  function [Out1,Out2] = mimocalc(Action,SLm)
% function [Out1,Out2] = mimocalc(Action,SLm)
% 
% Action == 'check'
%     looks at SLm.xcstate to see if a MIMO measurement is possible.
%     returns refch in Out1, respch in Out2  If Out1 is empty, not a MIMO measurement.
% Action == 'compute'
% Uses cspecs and aspecs as inputs in the SLm structure. 
% This means that a massive data request must be made b4 invoking this function. 
% The function will return an SLm structure with new (overwritten) xfers and coherences.
% If sucessful, status (in Out2) will be 1, if not, 0, and no change to SLm will be made. 
% SigLab Channel 1 is ALWAYS assumed to be an input. 
% 
% 
% Reference: Programming and Analysis for Digital Time Series Data
%            Loren D. Enochson, Robert K. Otnes
%            The Shock and Vibration Information Center
%            Navel Research Laboratory, Washington DC
%            SVM-3, 1968
%            pp 190-197 
% Dick Benson, DSP Technology 

switch Action
  case 'check'
      % examine the SLm.xcstate to see if MIMO appears feasible. 
      % a "requirement", albeit arbitrary, is that the cross functions 
      % below the diagonal (1,1 2,2 3,3 4,4 ) be enabled. 
      % Also, there should be no gaps in the array of cross channels. 
      % 
      last_refc = 0;
      Out1      = [];
      Out2      = [];
      
      if length(SLm.xcstate.refc) <2
         return;  % only one reference channel
      end;

      for refc =  SLm.xcstate.refc
          if refc-last_refc ~=1
           
             return; % a skipped channel in the reference channel chain
          end;
          xchans = sort([SLm.xcstate.resp(refc).r,refc]);
          if ~isempty(setdiff(1:length(xchans),xchans))
             return; % a skipped channel in the response channel chain
          end;
          last_refc=refc;
      end;
      
      % if execution get to this point, there appears to be a 
      % an array of cross functions that meets the MIMO requirements
      Out1  = SLm.xcstate.refc;
      Out2  = setdiff(xchans,SLm.xcstate.refc);

  case 'compute'

      nref = 4;   % set to max possible
      
      Out1 = SLm;   % copy input to output, then overwrite xfer and coherence
   
      % see how many rows of cspecs exist .... this will 
      % be equal to the number of reference channels (inputs to DUT) that are used 
      for i=1:3
          if isempty(SLm.xcmeas(i+1,1).cspec)
             nref = i;
             break;    % got an empty one .... 
          end;
      end;

      % nref now contains the number of references .... assuming no "holes" 
      % in the cspec array. 
      
      if nref==1
         return;  % single input system, nothing to be done 
      end;

      % check to see if all cross functions and aspecs exist to support this 
      % number of reference (input) channels.
      % Check that they are of proper length
      npoints = length(SLm.fdxvec);   % they must all be this length
      % find 1st cspec in row 1 (ref channel 1) that does not have required length ... 
      for chan = 2:16
         if length(SLm.xcmeas(1,chan).cspec) ~=npoints
            nchan = chan-1;
            break;
         end;
      end;
      
      
      

      % check the remainder of the cspecs and aspecs .... 
      for refc = 1:nref
          for chan = 1:nchan
              if chan==refc
                 if length(SLm.scmeas(chan).aspec) ~=npoints
                    Out2 = 0;   % status
                    s='Aspec size mismatch in mimocalc.m, no MIMO results will be produced.';
                    msgbox(s,'Operator Warning','warn','modal');
                    return
                 end;
              else
                 if length(SLm.xcmeas(refc,chan).cspec) ~=npoints
                    Out2 = 0;   % status
                    s='Cspec size mismatch in mimocalc.m, no MIMO results will be produced.';
                    msgbox(s,'Operator Warning','warn','modal');
                    return
                 end;
              end;
          end;
      end;

      % nref                      % number of reference channels (inputs to     DUT)
      % nresp     = nchan-nref    % number of response channels  (outputs from  DUT)
      respchans   = (nref+1):nchan;

      % If the program execution gets here, there should be a complete set of aspecs and 
      % cspecs to calculate the MIMO xfer functions and partial coherences. Let the complex fun begin ....
      % 
      % Compute results for each response channel on a frequency by frequency basis ... 
      % this is NOT a speedy operation ! 4 inputs, 4 outputs, 201 freq points takes 40 seconds (P5-90MHz)

   

      % create a "progress bar" to make time almost fly ... (well, I tried)
      xpb.pos    = [100 50 400 70];
      xpb.color  = [0 1 0];
      xpb.title  = 'MIMO Computation Progress';
      xpb.style  = 'modal';
      hpb        = progbar('init',xpb);

      for fi = 1:npoints         % frequency loop
          progbar('update',hpb,100*fi/npoints);    % to keep user from panic .... show progress !!! 
         
          for respc = respchans  % response channel loop
    
             % first, construct the "Gyxx" spectral matrix per pp 196 Enochson & Otnes
              Gyxx(1,1) = SLm.scmeas(respc).aspec(fi);  
        
              for refc = 1:nref
                  Gyxx(refc+1,1) = SLm.xcmeas(refc,respc).cspec(fi); % order is important due to conj.
                  Gyxx(1,refc+1) = conj(Gyxx(refc+1,1));
                  for k=1:nref
                      if k==refc
                         Gyxx(refc+1,refc+1) = SLm.scmeas(refc).aspec(fi);
                      else
                         Gyxx(refc+1,k+1)    = SLm.xcmeas(refc,k).cspec(fi);
                      end;
                  end;  % k loop
              end;  % refc loop
        
              % compute xfer function using elements of the Gyxx spectral matrix .... 
              % a pretty simple operation compared with computing the partial coherences. 
              Xfer = inv(Gyxx(2:nref+1,2:nref+1))*Gyxx(2:nref+1,1);
              for k=1:nref
                  Out1.xcmeas(k,respc).xfer(fi)=Xfer(k);
              end;
        
              % The (dreaded) partial coherence calculation. 
              % Need to cycle through each reference channel .... 
              for refc=1:nref
                  % permute the spectral matrix for each reference channel
                  Gyxxp(1,1)                =  Gyxx(1,1);
                  Gyxxp(1,2:nref+1)         =  refswap(Gyxx(1,2:nref+1),1,refc);
                  Gyxxp(2:nref+1,1)         =  Gyxxp(1,2:nref+1)';  % prime does conjugate    
                  Gyxxp(2:nref+1,2:nref+1)  =  refswap(Gyxx(2:nref+1,2:nref+1),1,refc);
        
                  % extract submatricies per eq 7.51 pp 196
                  % sigma_yy    = Gyxxp(1:2,1:2);
                  % sigma_y1    = Gyxxp(1:2,3:nref+1);
                  % sigma_1y    = sigma_y1';
                  % sigma_11    = Gyxxp(3:nref+1,3:nref+1);
                  % Gxy_p       = sigma_yy - sigma_y1*inv(sigma_11)*sigma_1y;
                  Gxy_p = Gyxxp(1:2,1:2) - Gyxxp(1:2,3:nref+1)*inv(Gyxxp(3:nref+1,3:nref+1))*(Gyxxp(1:2,3:nref+1)');
                  % absolute value needed to clean up small residual imaginary part 
                  partial_coh = abs((Gxy_p(1,2).*Gxy_p(2,1))/(Gxy_p(1,1)*Gxy_p(2,2))); 
                  Out1.xcmeas(refc,respc).coh(fi)=partial_coh;
               end;
          end;  % response channel loop 
      end; % findex frequency loop ... 
      progbar('close',hpb);
      Out2 = 1;   % status, we have success ... 
   otherwise
      disp('unrecognaized action in mimocalc.m ')

end;   % end switch (and function)



  function aout = refswap(ain,ix,ixp)
% function aout = refswap(ain,ix,ixp)
% swaps elements of input matrix or vector 
% index ix is swapped with index ixp
% e.g. say A is 3x3
% B=refswap(A,1,3) swaps all elements associated with index 1 with 
% those associated with index 3.
% Try:
% A = [1 2 3;4 5 6;7 8 9]; 
% B = refswap(A,1,3)
% 
     [nr,nc]=size(ain);
     if nr == 1 | nc == 1
        aout      = ain;
        aout(ixp) = ain(ix);
        aout(ix)  = ain(ixp);
     else
        index      = 1:nr;       
        index(ixp) = ix;
        index(ix)  = ixp;
        aout(index,index) = ain;  % aout(index,index) = ain(1:nr,1:nc);
     end;
% end function 


